"""receipt-ledger core: transaction <-> receipt matching, dedupe, gaps, advice.

Pure Python, no web framework. The plugin API layer (plugin_api.py) imports this,
which means the matching logic is testable without a gateway.

Storage layout (all paths configurable for tests):
  data/settings.json  -> user config (receipts dir, transactions csv, Securo endpoint)
  data/ledger.json    -> last scan result, so the pane renders instantly on open
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RECEIPT_LEDGER_DATA", PLUGIN_ROOT / "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
LEDGER_PATH = DATA_DIR / "ledger.json"

# Where each vendor's missing paper actually lives. Kept as data, not code, so the
# user can extend it without touching logic. Portal text is what we show in the UI.
VENDOR_PORTALS: dict[str, dict[str, str]] = {
    "hetzner": {"portal": "console.hetzner.cloud -> Billing -> Invoices", "kind": "hosting"},
    "hover": {"portal": "hover.com -> Account -> Billing -> Receipts", "kind": "domains"},
    "github": {"portal": "github.com -> Settings -> Billing -> Payment history", "kind": "code"},
    "openai": {"portal": "platform.openai.com -> Settings -> Billing -> Invoices", "kind": "ai"},
    "linkedin": {"portal": "linkedin.com -> Billing history", "kind": "ads"},
    "opencode": {"portal": "opencode.ai/console -> Settings -> Billing -> Invoices", "kind": "ai"},
    "anomaly": {"portal": "opencode.ai/console -> Settings -> Billing -> Invoices", "kind": "ai"},
    "openrouter": {"portal": "openrouter.ai -> Settings -> Credits -> Transaction history", "kind": "ai"},
    "replicate": {"portal": "replicate.com -> Billing -> Receipts", "kind": "ai"},
    "fireworks": {"portal": "fireworks.ai -> Settings -> Billing -> Invoices", "kind": "ai"},
    "microsoft": {"portal": "admin.microsoft.com -> Billing -> Bills and payments", "kind": "software"},
    "resend": {"portal": "resend.com -> Settings -> Invoices", "kind": "email"},
}

# Vendors that routinely mix personal and business spend. The advice engine flags
# these whenever the ledger shows the same vendor on more than one account.
SHARED_VENDORS = {"hover", "github", "openai", "microsoft", "anthropic", "apple", "google"}

RECEIPT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".csv", ".txt", ".eml"}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return abs(float(text))
    except ValueError:
        return None


def _to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _file_date(name: str) -> date | None:
    """Dates show up in receipt filenames in several shapes. Try the common ones."""
    for pattern, sep in (
        (r"(20\d{2})-(\d{2})-(\d{2})", "-"),
        (r"(\d{1,2})\.(\d{1,2})\.(\d{2})\b", "."),
        (r"(20\d{2})(\d{2})(\d{2})_", ""),
    ):
        match = re.search(pattern, name)
        if not match:
            continue
        text = sep.join(match.groups())
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%m.%d.%y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _file_amount(name: str) -> float | None:
    """Pull a currency amount out of a filename: '$50', '499.99', '_20.pdf'."""
    matches = re.findall(r"(?<![\d.])-?(\d{1,6}(?:\.\d{2}))(?![\d])", name.replace(",", ""))
    if matches:
        return abs(float(matches[-1]))
    return None


@dataclass
class Receipt:
    path: str
    filename: str
    sha256: str
    amount: float | None
    when: date | None
    vendor_guess: str
    duplicate_of: str | None = None


def scan_receipts(receipts_dir: Path) -> tuple[list[Receipt], list[Receipt]]:
    """Walk a folder of receipts. Returns (kept, duplicates) deduped by content hash."""
    kept: list[Receipt] = []
    duplicates: list[Receipt] = []
    if not receipts_dir or not receipts_dir.exists():
        return kept, duplicates

    seen: dict[str, str] = {}
    for path in sorted(p for p in receipts_dir.rglob("*") if p.is_file()):
        if path.suffix.lower() not in RECEIPT_EXTENSIONS:
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        when = _file_date(path.name)
        amount = _file_amount(path.name)
        vendor = ""
        name = _norm(path.name)
        for known in VENDOR_PORTALS:
            if known in name:
                vendor = known
                break
        receipt = Receipt(
            path=str(path),
            filename=path.name,
            sha256=digest,
            amount=amount,
            when=when,
            vendor_guess=vendor,
        )
        if digest in seen:
            receipt.duplicate_of = seen[digest]
            duplicates.append(receipt)
            continue
        seen[digest] = str(path)
        kept.append(receipt)
    return kept, duplicates


def load_transactions(csv_path: Path) -> list[dict[str, Any]]:
    """Read the transaction export. Header names are matched loosely."""
    if not csv_path or not csv_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            lowered = {_norm(k): v for k, v in raw.items() if k}
            when = _to_date(
                lowered.get("date")
                or lowered.get("posted")
                or lowered.get("transaction date")
                or lowered.get("posting date")
            )
            amount = _to_float(
                lowered.get("amount")
                or lowered.get("debit")
                or lowered.get("charge")
                or lowered.get("total")
            )
            vendor = (
                lowered.get("vendor")
                or lowered.get("description")
                or lowered.get("payee")
                or lowered.get("merchant")
                or ""
            ).strip()
            if not when or amount is None or not vendor:
                continue
            rows.append(
                {
                    "id": f"{when.isoformat()}::{_norm(vendor)[:24]}::{amount:.2f}",
                    "date": when.isoformat(),
                    "vendor": vendor,
                    "amount": amount,
                    "account": (lowered.get("account") or "").strip(),
                    "card": (lowered.get("card") or lowered.get("payment method") or "").strip(),
                    "note": (lowered.get("note") or lowered.get("memo") or "").strip(),
                    "business": (lowered.get("business") or "").strip(),
                }
            )
    return rows


def _score(txn: dict[str, Any], receipt: Receipt) -> float:
    """How sure are we that this receipt belongs to this transaction?"""
    if receipt.amount is None or abs(receipt.amount - txn["amount"]) > 0.02:
        return 0.0
    vendor = _norm(txn["vendor"])
    name = _norm(receipt.filename)
    score = 1.0
    if receipt.vendor_guess and receipt.vendor_guess in vendor:
        score += 2.0
    elif any(word and word in name for word in vendor.split() if len(word) > 3):
        score += 1.0
    txn_date = _to_date(txn["date"])
    if receipt.when and txn_date:
        gap = abs((receipt.when - txn_date).days)
        if gap > 45:
            return 0.0
        score += max(0.0, 2.0 - gap / 15.0)
    return score


def _assign(transactions: list[dict[str, Any]], receipts: list[Receipt]) -> dict[str, Receipt]:
    """Best-first global assignment.

    Greedy per-transaction matching steals a receipt for an earlier row and leaves
    the receipt's real month empty, so score every candidate pair and take the
    strongest matches first while both sides are still free.

    A weaker score is still honest when the amount is unambiguous: one transaction
    and one receipt share that exact amount, so the pairing cannot be wrong even
    without a date in the filename.
    """
    amount_counts: dict[str, int] = {}
    for txn in transactions:
        key = f"{txn['amount']:.2f}"
        amount_counts[key] = amount_counts.get(key, 0) + 1
    receipt_amounts: set[float] = set()
    duplicate_amounts: set[float] = set()
    for receipt in receipts:
        if receipt.amount is None:
            continue
        if receipt.amount in receipt_amounts:
            duplicate_amounts.add(receipt.amount)
        receipt_amounts.add(receipt.amount)

    scored: list[tuple[float, str, Receipt]] = []
    for txn in transactions:
        for receipt in receipts:
            value = _score(txn, receipt)
            if value >= 3.0:
                scored.append((value, txn["id"], receipt))
            elif value >= 2.0:
                amount_key = f"{txn['amount']:.2f}"
                if amount_counts.get(amount_key) == 1 and txn["amount"] not in duplicate_amounts:
                    scored.append((value, txn["id"], receipt))
    scored.sort(key=lambda item: item[0], reverse=True)

    assigned: dict[str, Receipt] = {}
    used: set[str] = set()
    for _, txn_id, receipt in scored:
        if txn_id in assigned or receipt.sha256 in used:
            continue
        assigned[txn_id] = receipt
        used.add(receipt.sha256)
    return assigned



def build_ledger(settings: dict[str, Any]) -> dict[str, Any]:
    """The whole scan: match, dedupe, list gaps, produce structural advice."""
    receipts_dir = Path(settings.get("receipts_dir") or "").expanduser()
    csv_path = Path(settings.get("transactions_csv") or "").expanduser()

    receipts, duplicates = scan_receipts(receipts_dir)
    transactions = load_transactions(csv_path)

    available = list(receipts)
    rows: list[dict[str, Any]] = []
    assigned = _assign(transactions, available)
    matched_paths = {receipt.path for receipt in assigned.values()}
    for txn in sorted(transactions, key=lambda t: t["date"], reverse=True):
        receipt = assigned.get(txn["id"])
        vendor_key = next((k for k in VENDOR_PORTALS if k in _norm(txn["vendor"])), "")
        portal = VENDOR_PORTALS.get(vendor_key, {})
        rows.append(
            {
                **txn,
                "status": "matched" if receipt else "missing",
                "receipt": receipt.path if receipt else None,
                "receipt_name": receipt.filename if receipt else None,
                "portal": portal.get("portal", ""),
                "category": portal.get("kind", ""),
            }
        )

    unmatched_receipts = [
        {"path": r.path, "filename": r.filename, "amount": r.amount, "vendor": r.vendor_guess}
        for r in receipts
        if r.path not in matched_paths
    ]
    matched = sum(1 for row in rows if row["status"] == "matched")
    missing = [row for row in rows if row["status"] == "missing"]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": {"receipts_dir": str(receipts_dir), "transactions_csv": str(csv_path)},
        "counts": {
            "transactions": len(rows),
            "matched": matched,
            "missing": len(rows) - matched,
            "receipts": len(receipts),
            "duplicates_dropped": len(duplicates),
            "unmatched_receipts": len(unmatched_receipts),
            "missing_dollars": round(sum(row["amount"] for row in missing), 2),
        },
        "transactions": rows,
        "missing": [
            {
                "date": row["date"],
                "vendor": row["vendor"],
                "amount": row["amount"],
                "portal": row["portal"],
            }
            for row in missing
        ],
        "duplicates": [
            {"filename": d.filename, "duplicate_of": Path(d.duplicate_of or "").name} for d in duplicates
        ],
        "unmatched_receipts": unmatched_receipts,
        "advice": build_advice(rows, len(duplicates)),
    }


def build_advice(rows: list[dict[str, Any]], duplicates_dropped: int) -> list[dict[str, str]]:
    """Structural fixes, ranked by what they save. Each item names the evidence."""
    advice: list[dict[str, str]] = []
    by_vendor: dict[str, set[str]] = {}
    missing_by_vendor: dict[str, int] = {}

    for row in rows:
        vendor = next((k for k in VENDOR_PORTALS if k in _norm(row["vendor"])), "")
        if vendor:
            accounts = by_vendor.setdefault(vendor, set())
            if row["account"]:
                accounts.add(row["account"].strip().lower())
            if row["status"] == "missing":
                missing_by_vendor[vendor] = missing_by_vendor.get(vendor, 0) + 1

    for vendor, accounts in sorted(by_vendor.items()):
        if len(accounts) > 1 and vendor in SHARED_VENDORS:
            advice.append(
                {
                    "kind": "split-vendor",
                    "title": f"Split {vendor} by entity",
                    "evidence": f"{vendor} is charged across {len(accounts)} accounts ({', '.join(sorted(accounts))}).",
                    "fix": f"Move the business {vendor} charges onto the business card and keep personal ones on a personal card.",
                }
            )

    personal = [row for row in rows if row["card"] and "business" not in row["card"].lower()]
    if personal:
        owed = round(sum(row["amount"] for row in personal), 2)
        advice.append(
            {
                "kind": "owner-reimbursement",
                "title": "Stop paying business costs from personal cards",
                "evidence": f"{len(personal)} business charges (${owed:,.2f}) sit on personal payment methods.",
                "fix": "Either move these to the business card or file an expense report so the company owes you explicitly.",
            }
        )

    for vendor, count in sorted(missing_by_vendor.items(), key=lambda kv: -kv[1]):
        if count >= 2:
            advice.append(
                {
                    "kind": "automate-vendor",
                    "title": f"Automate {vendor} receipts",
                    "evidence": f"{count} {vendor} charges have no receipt on file.",
                    "fix": f"Turn on emailed receipts for {vendor} and forward them to the receipt address so they land automatically.",
                }
            )

    if duplicates_dropped:
        advice.append(
            {
                "kind": "dedupe",
                "title": "Keep one receipt store",
                "evidence": f"{duplicates_dropped} duplicate files were dropped in this scan.",
                "fix": "Point every download at one folder instead of keeping copies in Dropbox and locally.",
            }
        )

    return advice


def load_settings() -> dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**load_settings(), **{k: v for k, v in settings.items() if v is not None}}
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2))
    return merged


def save_ledger(ledger: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2))


def load_ledger() -> dict[str, Any] | None:
    if LEDGER_PATH.exists():
        try:
            return json.loads(LEDGER_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return None
    return None


def scan(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    ledger = build_ledger(settings)
    save_ledger(ledger)
    return ledger
