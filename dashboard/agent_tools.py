"""Read-only answers from the last saved scan. Never scan, fetch, or save.

Only stdlib is required. When hosted by Hermes, its read-only config reader
supplies the managed settings layer; standalone use reads settings.json.
Amounts follow the ledger's charge convention, not net spend after credits.
"""
from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from functools import wraps
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RECEIPT_LEDGER_DATA", PLUGIN_ROOT / "data"))


def _managed_settings():
    try:
        from hermes_cli.config import load_config_readonly
    except ImportError:
        return {}
    config = load_config_readonly() or {}
    entry = ((config.get("plugins") or {}).get("entries") or {}).get("receipt-ledger") or {}
    return entry.get("settings") or {}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _settings():
    path = DATA_DIR / "settings.json"
    settings = _read(path) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError("Invalid settings")
    settings.update({k: v for k, v in _managed_settings().items() if v not in (None, "")})
    # Resolve only declared plugin secrets through Hermes' scoped secret API.
    for key, env in (("securo_token", "RECEIPT_LEDGER_SECURO_TOKEN"),
                     ("securo_password", "RECEIPT_LEDGER_SECURO_PASSWORD")):
        try:
            from agent.secret_scope import get_secret
        except ImportError:
            value = os.environ.get(env)
        else:
            value = get_secret(env)
        if value:
            settings[key] = value
    return settings


def _answer(fn):
    @wraps(fn)
    def safe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            # Do not expose file contents, credentials, or exception text.
            return {"ok": False, "summary": "Cannot read the saved ledger or query arguments. Check Receipt Ledger settings, use valid filters, and run a scan in the plugin."}
    return safe


def _snapshot():
    settings = _settings()
    configured = bool(settings.get("transactions_csv") or
                      (settings.get("securo_url") and (settings.get("securo_token") or
                       (settings.get("securo_email") and settings.get("securo_password")))))
    state = {"configured": configured,
             "source": "securo" if settings.get("securo_url") else "files",
             "period": {"from": settings.get("from_date"), "to": settings.get("to_date")},
             "last_scan": None}
    # A saved scan is evidence and stays answerable even when the connection is
    # not configured right now (token revoked, URL cleared). The state says so.
    path = DATA_DIR / "ledger.json"
    if not path.exists():
        if not configured:
            return None, {"ok": False, **state, "summary": "Configure Receipt Ledger with Securo credentials or a transactions CSV, then run a scan in the plugin."}
        return None, {"ok": False, **state, "summary": "No saved scan exists. Run a scan in Receipt Ledger first; these tools never contact Securo."}
    ledger = _read(path)
    if not isinstance(ledger, dict) or not isinstance(ledger.get("transactions"), list) or not ledger.get("generated_at"):
        raise ValueError("Invalid ledger")
    source = ledger.get("source") or {}
    rows = ledger["transactions"]
    dates = [row["date"] for row in rows]
    state.update(source=source.get("kind", state["source"]), last_scan=ledger["generated_at"],
                 period={"from": source.get("from") or min(dates, default=None),
                         "to": source.get("to") or max(dates, default=None)})
    return ledger, {"ok": True, **state}


def _amount(row):
    value = Decimal(str(row["amount"]))
    if not value.is_finite() or value < 0:
        raise ValueError("Invalid charge amount")
    return value


def _dollars(rows):
    return float(sum((_amount(row) for row in rows), Decimal(0)).quantize(Decimal("0.01")))


def _charges(ledger, from_date=None, to_date=None, vendor=None, category=None):
    for value in (from_date, to_date):
        if value is not None:
            if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
                raise ValueError("Use YYYY-MM-DD")
    if from_date and to_date and from_date > to_date:
        raise ValueError("Reversed date range")
    rows = []
    for row in ledger["transactions"]:
        if row["kind"] != "charge":
            continue
        date.fromisoformat(row["date"])
        _amount(row)
        if from_date and row["date"] < from_date or to_date and row["date"] > to_date:
            continue
        if vendor is not None and vendor.casefold() not in row.get("vendor", "").casefold():
            continue
        # category_kind holds the vendor-derived category when Securo/CSV has none.
        if category is not None and category.casefold() not in (row.get("category") or row.get("category_kind") or "").casefold():
            continue
        if row["status"] not in ("attached", "matched", "missing"):
            raise ValueError("Unknown receipt state")
        rows.append(dict(row))
    return sorted(rows, key=lambda row: (row["date"], str(row.get("id", ""))), reverse=True)


@_answer
def spend(by="vendor", from_date=None, to_date=None, vendor=None, category=None):
    """Charge totals, counts, and evidence rows; dates inclusive, text contains."""
    if by not in ("vendor", "category", "month", "account"):
        return {"ok": False, "summary": "Group spend by vendor, category, month, or account."}
    ledger, state = _snapshot()
    if ledger is None:
        return state
    rows = _charges(ledger, from_date, to_date, vendor, category)
    buckets = {}
    for row in rows:
        key = row["date"][:7] if by == "month" else (row.get(by) or (row.get("category_kind") if by == "category" else None) or "Uncategorized")
        buckets.setdefault(key, []).append(row)
    totals = [{"key": key, "count": len(items), "dollars": _dollars(items)} for key, items in buckets.items()]
    totals.sort(key=lambda item: item["key"] if by == "month" else (-item["dollars"], item["key"]))
    dollars = _dollars(rows)
    return {**state, "by": by, "count": len(rows), "dollars": dollars, "totals": totals, "rows": rows,
            "filters": {"from_date": from_date, "to_date": to_date, "vendor": vendor, "category": category},
            "summary": f"{len(rows)} charges total ${dollars:,.2f}, grouped by {by}, from the saved scan {state['last_scan']}. Credits excluded; no currency conversion."}


@_answer
def missing_paper(min_amount=0, limit=50):
    """Missing charges at or above min_amount, newest first, with saved portals."""
    minimum = Decimal(str(min_amount))
    if not minimum.is_finite() or minimum < 0 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        return {"ok": False, "summary": "Use a nonnegative min_amount and an integer limit from 1 to 500."}
    ledger, state = _snapshot()
    if ledger is None:
        return state
    missing = [row for row in _charges(ledger) if row["status"] == "missing" and _amount(row) >= minimum]
    rows = [{**row, "portal": row.get("portal") or ""} for row in missing[:limit]]
    return {**state, "rows": rows, "count": len(rows), "total_count": len(missing), "has_more": len(missing) > len(rows),
            "missing_dollars": _dollars(missing),
            "summary": f"{len(missing)} charges of at least ${minimum:,.2f} lack paper. Showing {len(rows)} newest first; portals are from the saved scan."}


@_answer
def receipt_coverage(from_date=None, to_date=None):
    """Receipt coverage by charge count, excluding credits, over inclusive dates."""
    ledger, state = _snapshot()
    if ledger is None:
        return state
    rows = _charges(ledger, from_date, to_date)
    missing = [row for row in rows if row["status"] == "missing"]
    charges = len(rows)
    with_paper = charges - len(missing)
    coverage = round(100 * with_paper / charges, 1) if charges else 0.0
    dollars = _dollars(missing)
    return {**state, "charges": charges, "with_paper": with_paper, "missing": len(missing),
            "missing_dollars": dollars, "coverage_pct": coverage,
            "filters": {"from_date": from_date, "to_date": to_date},
            "summary": f"{with_paper} of {charges} charges have paper ({coverage:.1f}%); {len(missing)} lack paper (${dollars:,.2f}) in the saved scan."}


@_answer
def status():
    """Configuration and provenance only; never return credentials or fetch data."""
    ledger, state = _snapshot()
    if ledger is None:
        return state
    return {**state, "summary": f"Receipt Ledger is configured for {state['source']}; last scan {state['last_scan']}, period {state['period']['from']} to {state['period']['to']}. Answers use this cached scan only."}
