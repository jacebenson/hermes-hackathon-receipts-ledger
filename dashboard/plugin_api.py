"""receipt-ledger — backend half of the Hermes desktop plugin.

Mounts at /api/plugins/receipt-ledger/ and gives the pane everything it cannot do
from the renderer: reading a receipts folder, parsing the transaction export, and
writing an accountant package.

Routes
  GET  /status     -> is it configured, what did the last scan find
  GET  /ledger     -> last scan result (cheap, renders instantly on pane open)
  POST /scan       -> rescan now, returns the fresh ledger
  GET  /settings   -> current config
  POST /settings   -> save config, then rescan
  POST /package    -> write a zip for the accountant; returns path + file count

Deliberately no Securo calls yet: the network adapter lands behind the same
`transactions` interface once a token exists, so nothing here has to change.
"""

from __future__ import annotations

import csv
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request

import ledger_core as core

router = APIRouter()


@router.get("/status")
async def status() -> dict:
    settings = core.load_settings()
    ledger = core.load_ledger()
    configured = bool(settings.get("receipts_dir") and settings.get("transactions_csv"))
    return {
        "configured": configured,
        "settings": settings,
        "last_scan": (ledger or {}).get("generated_at"),
        "counts": (ledger or {}).get("counts", {}),
        "has_advice": bool((ledger or {}).get("advice")),
    }


@router.get("/ledger")
async def ledger() -> dict:
    data = core.load_ledger()
    if data is None:
        return {"generated_at": None, "counts": {}, "transactions": [], "missing": [], "advice": []}
    return data


@router.post("/scan")
async def scan() -> dict:
    return core.scan()


@router.get("/settings")
async def get_settings() -> dict:
    return core.load_settings()


@router.post("/settings")
async def put_settings(request: Request) -> dict:
    body = await request.json()
    allowed = {"receipts_dir", "transactions_csv", "securo_url", "securo_token_ref", "entity"}
    core.save_settings({k: v for k, v in (body or {}).items() if k in allowed})
    return core.scan()


@router.post("/package")
async def package(request: Request) -> dict:
    """Copy every matched receipt into a dated folder and zip it for the accountant."""
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — body is optional
        body = {}
    ledger = core.load_ledger() or core.scan()
    stamp = datetime.now().strftime("%Y-%m-%d")
    staged = Path(body.get("output_dir") or core.DATA_DIR / "packages" / stamp)
    receipts_out = staged / "receipts"
    receipts_out.mkdir(parents=True, exist_ok=True)

    copied = []
    for row in ledger.get("transactions", []):
        source = row.get("receipt")
        if not source or not Path(source).exists():
            continue
        target = receipts_out / f"{row['date']} {row['vendor'][:40]} ${row['amount']:.2f}{Path(source).suffix}"
        shutil.copy2(source, target)
        copied.append({"file": target.name, "date": row["date"], "vendor": row["vendor"], "amount": row["amount"]})

    with (staged / "receipt-index.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "vendor", "amount", "file"])
        writer.writeheader()
        writer.writerows(copied)

    with (staged / "still-missing.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "vendor", "amount", "portal"])
        writer.writeheader()
        writer.writerows(ledger.get("missing", []))

    archive = staged.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in staged.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(staged))

    return {
        "archive": str(archive),
        "folder": str(staged),
        "receipts": len(copied),
        "still_missing": len(ledger.get("missing", [])),
    }
