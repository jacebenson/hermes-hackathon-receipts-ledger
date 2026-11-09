"""receipt-ledger — backend half of the Hermes desktop plugin.

Mounts at /api/plugins/receipt-ledger/ and gives the pane everything it cannot do
from the renderer: talking to a Securo instance, reading a receipts folder,
parsing a transaction export, and writing an accountant package.

Routes
  GET  /status      -> configured? which mode? what did the last scan find
  GET  /ledger      -> last scan result (cheap, renders instantly on pane open)
  POST /scan        -> rescan now, returns the fresh ledger
  GET  /settings    -> current config, secrets replaced by "is it set" flags
  POST /settings    -> save config, then rescan
  POST /securo/test -> try a URL + credential without saving it
  POST /package     -> download receipts, write the accountant zip; returns path

Secrets never leave this process. The renderer only ever learns whether a
credential is set, never its value.
"""

from __future__ import annotations

import csv
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# Hermes imports this file by path (importlib spec_from_file_location), which does
# not put its own directory on sys.path, so the sibling modules below would fail
# with "No module named 'audit'". Make the plugin self-locating instead of relying
# on how the host loads it.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import audit
import classify as classification
import jurisdiction_data
import ledger_core as core
import securo_client
import securo_write
from securo_client import SecuroError

router = APIRouter()

INDEX_FIELDS = ["date", "vendor", "amount", "category", "account", "source", "paper", "file"]


def _failure(error: SecuroError, ledger: dict | None = None) -> JSONResponse:
    message = str(error)
    if error.hint:
        message = f"{message} {error.hint}"
    payload = {"detail": message, "error": str(error), "hint": error.hint, "status": error.status}
    if ledger is not None:
        payload["ledger"] = ledger
    return JSONResponse(status_code=502, content=payload)


@router.get("/status")
async def status() -> dict:
    settings = core.effective_settings()
    state = core.configured(settings)
    ledger = core.load_ledger() or {}
    from_date, to_date = core.securo_period(settings)
    return {
        **state,
        "settings": core.public_settings(settings),
        "period": {"from": from_date, "to": to_date},
        "last_scan": ledger.get("generated_at"),
        "source": ledger.get("source", {}),
        "counts": ledger.get("counts", {}),
        "has_advice": bool(ledger.get("advice")),
    }


@router.get("/ledger")
async def ledger() -> dict:
    data = core.load_ledger()
    if data is None:
        return {
            "generated_at": None,
            "counts": {},
            "transactions": [],
            "missing": [],
            "totals": {},
            "advice": [],
        }
    return data


@router.get("/audit")
async def audit_log() -> dict:
    return audit.summary()


@router.post("/audit/clear")
async def audit_clear() -> dict:
    return {"ok": audit.clear()}


@router.get("/jurisdictions")
async def jurisdiction_bundle(state_id: str = "") -> dict:
    """The whole catalogue, plus one record when an id is asked for."""
    data = jurisdiction_data.summary()
    chosen = jurisdiction_data.record(state_id) if state_id else None
    return {**data, "record": chosen}


@router.post("/classification/rule")
async def classification_rule(request: Request) -> dict:
    """A human decision, stored as a standing rule. The only way a charge is
    ever classified without review."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body is a user error, not a crash
        body = {}
    if not isinstance(body, dict):
        body = {}
    match = body.get("match")
    decision = body.get("decision")
    if not isinstance(match, dict) or not match:
        return {"ok": False, "error": "A rule needs at least one match field (vendor, account, card or category)."}
    rule = {
        "decision": decision,
        "match": match,
        "business_pct": body.get("business_pct"),
        "basis": (body.get("basis") or "").strip(),
        "note": (body.get("note") or "").strip(),
        "created_by": (body.get("created_by") or "owner").strip() or "owner",
    }
    if body.get("id"):
        rule["id"] = str(body["id"])
    try:
        saved = classification.save_rule(rule, path=core.DATA_DIR / "classification.json")
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    audit.record("settings", detail="classification rule saved", target=str(saved.get("decision", "")))
    # Re-apply to the cached charges. Deliberately no network call: a rule must
    # never force a live Securo fetch.
    cached = core.load_ledger()
    if cached:
        rules = classification.load_rules(core.DATA_DIR / "classification.json")
        rows = classification.classify(cached.get("transactions", []), rules)
        cached["transactions"] = rows
        cached["classification_summary"] = classification.summary(rows)
        cached["review_queue"] = [row for row in rows if row.get("kind", "charge") == "charge" and row["review"]["needed"]]
        core.save_ledger(cached)
    return {"ok": True, "rule": saved}


@router.get("/classification")
async def classification_view() -> dict:
    engine = core.classification_engine
    rules = engine.load_rules(core.DATA_DIR / "classification.json")
    rows = engine.classify((core.load_ledger() or {}).get("transactions", []), rules)
    return {"summary": engine.summary(rows),
            "queue": [row for row in rows if row.get("kind", "charge") == "charge" and row["review"]["needed"]],
            "rules": rules}


@router.post("/scan")
async def scan():
    cached = core.load_ledger()
    try:
        result = core.scan()
    except SecuroError as error:
        audit.record("scan", ok=False, detail=str(error))
        return _failure(error, cached)
    counts = result.get("counts") or {}
    audit.record(
        "scan",
        counts={"charges": counts.get("charges"), "with_paper": counts.get("with_paper"), "missing": counts.get("missing")},
        detail=f"{(result.get('source') or {}).get('kind', 'files')} scan",
        target=(result.get("source") or {}).get("securo_url", ""),
    )
    return result


@router.get("/settings")
async def get_settings() -> dict:
    return core.public_settings()


@router.post("/settings")
async def put_settings(request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — body is optional
        body = {}
    core.save_settings(body or {})
    settings = core.effective_settings()
    state = core.configured(settings)
    if not state["configured"]:
        return {"settings": core.public_settings(settings), **state, "ledger": None}
    try:
        return {"settings": core.public_settings(settings), **state, "ledger": core.scan()}
    except SecuroError as error:
        return _failure(error, core.load_ledger())


@router.post("/securo/test")
async def securo_test(request: Request) -> dict:
    """Prove a URL and credential work before saving them.

    Anything omitted falls back to what is already saved, so "Test" works both on
    a fresh form and on an unchanged one.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    saved = core.effective_settings()
    overrides = {key: value for key, value in (body or {}).items() if value not in ("", None)}
    merged = {**saved, **overrides}
    try:
        client = securo_client.client_for(merged)
        result = client.check()
        from_date, to_date = core.securo_period(merged)
        result["period"] = {"from": from_date, "to": to_date}
        return result
    except SecuroError as error:
        return {"ok": False, **error.as_dict()}


def _attachment_target(body: dict) -> tuple:
    """Resolve only a scanned Securo target, then recheck it and the local file."""
    transaction_id, path = body.get("transaction_id"), body.get("path")
    if not isinstance(transaction_id, str) or not transaction_id or not isinstance(path, str) or not path:
        raise SecuroError("transaction_id and path must be nonempty strings.", hint="Select a scanned transaction and a local receipt file.")
    ledger = core.load_ledger() or {}
    source = ledger.get("source") or {}
    if source.get("kind") != "securo":
        raise SecuroError("No Securo scan is available.", hint="Run a Securo scan before attaching a receipt.")
    row = next((row for row in ledger.get("transactions", []) if row.get("securo_id") and transaction_id in (row.get("id"), row.get("securo_id"))), None)
    if row is None:
        raise SecuroError("The transaction is not in the last Securo scan.", hint="Rescan and select the transaction again.")
    file, size = securo_write.validate_file(path)
    if file.suffix.lower() not in core.RECEIPT_EXTENSIONS:
        raise SecuroError("This receipt file extension is not allowed.", hint="Use one of: " + ", ".join(sorted(core.RECEIPT_EXTENSIONS)))
    client = securo_client.client_for(core.effective_settings())
    if source.get("securo_url") != client.base_url:
        raise SecuroError("The Securo instance changed since the last scan.", hint="Rescan the current instance before attaching a receipt.")
    try:
        client._ensure_token()
        live = client._call("GET", f"/api/transactions/{quote(row['securo_id'], safe='')}")
        if not isinstance(live, dict) or str(live.get("id")) != row["securo_id"]:
            raise securo_write.request_error(404)
        count = len(client.attachments(row["securo_id"]))
    except SecuroError as error:
        raise securo_write.request_error(error.status) from error
    except (AttributeError, TypeError, ValueError, OSError) as error:
        raise SecuroError("Securo returned unusable transaction or attachment data.", hint="Check the Securo URL and version, then rescan before attaching.") from error
    return client, row, file, size, count


@router.post("/securo/attach/preview")
async def securo_attach_preview(request: Request) -> dict:
    """Read-only preview. Login may POST for authentication, never for attachments."""
    try:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise SecuroError("Expected a JSON object.", hint="Send transaction_id and path in a JSON object.")
        _, row, file, size, count = _attachment_target(body)
        return {
            "ok": True,
            "target": {key: row[key] for key in ("date", "vendor", "amount")},
            "file": {"name": file.name, "bytes": size},
            "attachment_count": count,
            "warnings": [f"This transaction already has {count} attachment(s). This upload adds another; it does not replace them."] if count else [],
        }
    except SecuroError as error:
        return {"ok": False, **error.as_dict()}


@router.post("/securo/attach")
async def securo_attach(request: Request) -> dict:
    """One explicitly confirmed write, with the same checks repeated at upload."""
    body = None
    try:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise SecuroError("Expected a JSON object.", hint="Send transaction_id, path and confirm: true in a JSON object.")
        if body.get("confirm") is not True:
            raise SecuroError("Attachment upload requires confirm: true.", hint="Preview the target and receipt, then explicitly confirm the upload.")
        client, row, file, _, _ = _attachment_target(body)
        result = securo_write.SecuroWriter(client).upload_attachment(row["securo_id"], file)
        audit.record(
            "attach",
            detail=f"attached {result['filename']}",
            counts={"bytes": result["size"]},
            target=f"{row['date']} {row['vendor']}"[:200],
        )
        return {"ok": True, "attached": {key: result[key] for key in ("filename", "size")}, "transaction_id": row["securo_id"]}
    except SecuroError as error:
        audit.record("attach", ok=False, detail=str(error), target=str((body or {}).get("transaction_id", ""))[:200])
        return {"ok": False, **error.as_dict()}


@router.post("/package")
async def package(request: Request):
    """Copy every receipt into a dated folder and zip it for the accountant.

    Securo attachments are fetched on demand and cached under data/attachments,
    so a second build of the same month costs nothing.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    settings = core.effective_settings()
    ledger = core.load_ledger()
    if ledger is None:
        try:
            ledger = core.scan()
        except SecuroError as error:
            return _failure(error)

    stamp = datetime.now().strftime("%Y-%m-%d")
    staged = Path(body.get("output_dir") or core.DATA_DIR / "packages" / stamp).expanduser()
    receipts_out = staged / "receipts"
    receipts_out.mkdir(parents=True, exist_ok=True)

    client = None
    if (ledger.get("source") or {}).get("kind") == "securo":
        try:
            client = securo_client.client_for(settings)
        except SecuroError as error:
            return _failure(error, ledger)

    copied: list[dict] = []
    errors: list[str] = []
    for row in ledger.get("transactions", []):
        target = receipts_out / core.safe_name(
            f"{row['date']} {row['vendor'][:40]} ${row['amount']:.2f}", ".pdf"
        )
        if row["status"] == "attached" and client is not None and row.get("securo_id"):
            try:
                attachments = client.attachments(row["securo_id"])
            except SecuroError as error:
                errors.append(f"{row['vendor']} {row['date']}: {error}")
                continue
            for index, attachment in enumerate(attachments):
                attachment_id = str(attachment.get("id") or "")
                filename = attachment.get("filename") or f"receipt-{index}"
                cached = core.cached_attachment(row["securo_id"], attachment_id, filename)
                if not cached.exists():
                    try:
                        _, _, payload = client.download_attachment(row["securo_id"], attachment_id)
                    except SecuroError as error:
                        errors.append(f"{row['vendor']} {row['date']}: {error}")
                        continue
                    cached.parent.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(payload)
                file_target = receipts_out / core.safe_name(
                    f"{row['date']} {row['vendor'][:40]} ${row['amount']:.2f}"
                    + (f" {index}" if index else ""),
                    Path(filename).suffix or ".pdf",
                )
                shutil.copy2(cached, file_target)
                copied.append(
                    {
                        "date": row["date"],
                        "vendor": row["vendor"],
                        "amount": row["amount"],
                        "category": row.get("category", ""),
                        "account": row.get("account", ""),
                        "source": "securo",
                        "paper": "attachment",
                        "file": file_target.name,
                    }
                )
            continue
        if row["status"] == "matched" and row.get("receipt"):
            source = Path(row["receipt"])
            if not source.exists():
                errors.append(f"{row['vendor']} {row['date']}: {source} disappeared")
                continue
            file_target = receipts_out / core.safe_name(
                f"{row['date']} {row['vendor'][:40]} ${row['amount']:.2f}",
                source.suffix,
            )
            shutil.copy2(source, file_target)
            copied.append(
                {
                    "date": row["date"],
                    "vendor": row["vendor"],
                    "amount": row["amount"],
                    "category": row.get("category", ""),
                    "account": row.get("account", ""),
                    "source": row.get("source", "csv"),
                    "paper": "file",
                    "file": file_target.name,
                }
            )

    with (staged / "receipt-index.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(copied)

    missing = ledger.get("missing", [])
    with (staged / "still-missing.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "vendor", "amount", "portal"])
        writer.writeheader()
        writer.writerows(missing)

    totals = ledger.get("totals", {})
    with (staged / "totals.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["dimension", "key", "charges", "dollars"])
        for dimension in ("by_vendor", "by_category", "by_month", "by_account"):
            for bucket in totals.get(dimension, []):
                writer.writerow([dimension, bucket["key"], bucket["count"], f"{bucket['dollars']:.2f}"])

    engine = core.classification_engine
    rules = engine.load_rules(core.DATA_DIR / "classification.json")
    classified = engine.classify(ledger.get("transactions", []), rules)
    with (staged / "classification.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=engine.EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(engine.export_row(row) for row in classified)

    counts = ledger.get("counts", {})
    source = ledger.get("source", {})
    (staged / "README.txt").write_text(
        "\n".join(
            [
                "Receipt ledger package",
                f"Built {datetime.now().isoformat(timespec='seconds')}",
                "",
                f"Charges:            {counts.get('charges', 0)}",
                f"With paper:         {counts.get('with_paper', 0)}",
                f"Missing paper:      {counts.get('missing', 0)}",
                f"Missing dollars:    ${counts.get('missing_dollars', 0):,.2f}",
                f"Sources:            {source.get('kind', 'files')}"
                + (f" ({source.get('securo_url')}, {source.get('from')} to {source.get('to')})" if source.get("securo_url") else ""),
                "",
                "receipts/            one file per matched charge, named date vendor amount",
                "receipt-index.csv    every receipt with its charge, category and account",
                "still-missing.csv    charges with no paper found, and where to get it",
                "totals.csv           spend by vendor, category, month and account",
                "",
                "Nothing here is a tax determination. The plugin matches paper to charges;",
                "you and your accountant decide what is deductible.",
            ]
        )
        + "\n"
    )

    audit.record(
        "package",
        detail=f"wrote {len(copied)} receipts and {len(missing)} still-missing rows",
        counts={"receipts": len(copied), "still_missing": len(missing), "errors": len(errors)},
        target=str(staged),
    )

    archive = staged.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in staged.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(staged))

    return {
        "archive": str(archive),
        "folder": str(staged),
        "receipts": len(copied),
        "still_missing": len(missing),
        "errors": errors,
    }
