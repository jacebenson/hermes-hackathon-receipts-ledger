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
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import ledger_core as core
import securo_client
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
    settings = core.load_settings()
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


@router.post("/scan")
async def scan():
    cached = core.load_ledger()
    try:
        return core.scan()
    except SecuroError as error:
        return _failure(error, cached)


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
    settings = core.load_settings()
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
    saved = core.load_settings()
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
    settings = core.load_settings()
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
