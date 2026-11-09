"""receipt-ledger core: transaction <-> receipt matching, dedupe, gaps, advice.

Pure Python, no web framework. The plugin API layer (plugin_api.py) imports this,
which means the matching logic is testable without a gateway.

Two ways in, same ledger out:

  Securo   charges and their attachments come from a self-hosted Securo instance
           (see securo_client.py). An attachment on a charge is proof of paper —
           no guessing, no filename archaeology.
  Files    a folder of receipts plus a CSV export. Names and amounts are read,
           nothing is uploaded.

When both are configured the Securo charges win and the folder is used to fill
gaps, so an old receipt dump still counts.

Storage layout (all paths configurable for tests):
  data/settings.json  -> user config (Securo endpoint/credential, folder, CSV)
  data/ledger.json    -> last scan result, so the pane renders instantly on open
  data/attachments/   -> receipts pulled out of Securo for the accountant package
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import securo_client
from securo_client import SecuroError

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RECEIPT_LEDGER_DATA", PLUGIN_ROOT / "data"))
SETTINGS_PATH = DATA_DIR / "settings.json"
LEDGER_PATH = DATA_DIR / "ledger.json"
ATTACHMENT_DIR = DATA_DIR / "attachments"

SETTINGS_KEYS = (
    "securo_url",
    "securo_token",
    "securo_email",
    "securo_password",
    "securo_workspace_id",
    "securo_timeout",
    "receipts_dir",
    "transactions_csv",
    "from_date",
    "to_date",
)
SECRET_KEYS = ("securo_token", "securo_password")

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
    "ringcentral": {"portal": "ringcentral.com -> Billing -> Invoices", "kind": "phone"},
    "google": {"portal": "payments.google.com -> Subscriptions and services", "kind": "software"},
    "apple": {"portal": "reportaproblem.apple.com -> Purchase history", "kind": "software"},
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


def scan_receipts(receipts_dir: Path | None) -> tuple[list[Receipt], list[Receipt]]:
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


def load_transactions(csv_path: Path | None) -> list[dict[str, Any]]:
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
                    "description": lowered.get("description") or vendor,
                    "amount": amount,
                    "account": (lowered.get("account") or "").strip(),
                    "card": (lowered.get("card") or lowered.get("payment method") or "").strip(),
                    "category": (lowered.get("category") or "").strip(),
                    "note": (lowered.get("note") or lowered.get("memo") or "").strip(),
                    "business": (lowered.get("business") or "").strip(),
                    "kind": "charge",
                    "attachment_count": 0,
                    "source": "csv",
                }
            )
    return rows


# -- Securo ---------------------------------------------------------------


def securo_period(settings: dict[str, Any]) -> tuple[str, str]:
    """The window to ask Securo for: explicit settings, else this calendar year."""
    today = date.today()
    from_date = _to_date(settings.get("from_date")) or date(today.year, 1, 1)
    to_date = _to_date(settings.get("to_date")) or today
    return from_date.isoformat(), to_date.isoformat()


def map_securo_transaction(raw: dict[str, Any], accounts: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Turn one Securo transaction into the ledger's row shape."""
    when = _to_date(raw.get("date") or raw.get("effective_bill_date"))
    amount = _to_float(raw.get("amount_primary"))
    if amount is None:
        amount = _to_float(raw.get("amount"))
    if when is None or amount is None:
        return None

    account = accounts.get(str(raw.get("account_id") or ""), {})
    description = (raw.get("description") or raw.get("original_description") or "").strip()
    vendor = (
        (raw.get("payee_name") or "").strip()
        or (raw.get("payee") or "").strip()
        or description
    )
    category = raw.get("category") or {}
    category_name = (category.get("name") if isinstance(category, dict) else "") or ""
    tx_type = (raw.get("type") or "").lower()
    credit = tx_type == "credit" or float(raw.get("amount") or 0) < 0
    try:
        attachment_count = int(raw.get("attachment_count") or 0)
    except (TypeError, ValueError):
        attachment_count = 0

    return {
        "id": f"securo:{raw.get('id')}",
        "securo_id": str(raw.get("id") or ""),
        "date": when.isoformat(),
        "vendor": vendor,
        "description": description,
        "amount": amount,
        "account": account.get("name") or "",
        "card": account.get("masked_number") or account.get("institution") or "",
        "category": category_name,
        "note": (raw.get("notes") or "").strip(),
        "business": "",
        "kind": "credit" if credit else "charge",
        "attachment_count": attachment_count,
        "currency": raw.get("currency") or "",
        "status_remote": raw.get("status") or "",
        "source": "securo",
    }


def securo_transactions(settings: dict[str, Any], *, client: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pull the charge window out of Securo, plus a small provenance block."""
    client = client or securo_client.client_for(settings)
    from_date, to_date = securo_period(settings)
    accounts = {row["id"]: row for row in client.accounts() if row.get("id")}
    raw_rows = client.transactions(from_date, to_date)
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        mapped = map_securo_transaction(raw, accounts)
        if mapped is not None:
            rows.append(mapped)
    return rows, {
        "kind": "securo",
        "securo_url": client.base_url,
        "from": from_date,
        "to": to_date,
        "remote_count": len(raw_rows),
        "accounts": len(accounts),
    }


# -- matching -------------------------------------------------------------


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


def _portal_for(vendor: str) -> dict[str, str]:
    key = next((known for known in VENDOR_PORTALS if known in _norm(vendor)), "")
    return VENDOR_PORTALS.get(key, {"portal": "", "kind": ""})


def build_ledger(settings: dict[str, Any], *, securo_client_: Any = None) -> dict[str, Any]:
    """The whole scan: fetch, match, dedupe, list gaps, produce structural advice."""
    receipts_dir = Path(settings["receipts_dir"]).expanduser() if settings.get("receipts_dir") else None
    csv_path = Path(settings["transactions_csv"]).expanduser() if settings.get("transactions_csv") else None

    source: dict[str, Any]
    transactions: list[dict[str, Any]]
    if settings.get("securo_url"):
        transactions, source = securo_transactions(settings, client=securo_client_)
        if csv_path and csv_path.exists():
            seen = {(row["date"], round(row["amount"], 2), _norm(row["vendor"])[:16]) for row in transactions}
            extra = [
                row
                for row in load_transactions(csv_path)
                if (row["date"], round(row["amount"], 2), _norm(row["vendor"])[:16]) not in seen
            ]
            transactions.extend(extra)
            source["csv_added"] = len(extra)
    else:
        transactions = load_transactions(csv_path)
        source = {
            "kind": "files",
            "transactions_csv": str(csv_path) if csv_path else "",
            "receipts_dir": str(receipts_dir) if receipts_dir else "",
        }

    receipts, duplicates = scan_receipts(receipts_dir)
    assigned = _assign(transactions, list(receipts))
    matched_paths = {receipt.path for receipt in assigned.values()}

    rows: list[dict[str, Any]] = []
    for txn in sorted(transactions, key=lambda item: item["date"], reverse=True):
        receipt = assigned.get(txn["id"])
        portal = _portal_for(txn["vendor"])
        attachment_count = int(txn.get("attachment_count") or 0)
        if attachment_count:
            status, paper = "attached", "securo"
        elif receipt is not None:
            status, paper = "matched", "file"
        else:
            status, paper = "missing", None
        rows.append(
            {
                **txn,
                "status": status,
                "paper": paper,
                "receipt": (receipt.path if receipt else None),
                "receipt_name": (receipt.filename if receipt else None),
                "portal": portal.get("portal", ""),
                "category_kind": portal.get("kind", ""),
            }
        )

    unmatched_receipts = [
        {"path": r.path, "filename": r.filename, "amount": r.amount, "vendor": r.vendor_guess}
        for r in receipts
        if r.path not in matched_paths
    ]
    charges = [row for row in rows if row["kind"] == "charge"]
    with_paper = [row for row in charges if row["status"] in ("attached", "matched")]
    missing = [row for row in charges if row["status"] == "missing"]
    attached = sum(1 for row in rows if row["status"] == "attached")

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": source,
        "counts": {
            "transactions": len(rows),
            "charges": len(charges),
            "matched": sum(1 for row in rows if row["status"] == "matched"),
            "attached": attached,
            "with_paper": len(with_paper),
            "missing": len(missing),
            "receipts": len(receipts),
            "duplicates_dropped": len(duplicates),
            "unmatched_receipts": len(unmatched_receipts),
            "missing_dollars": round(sum(row["amount"] for row in missing), 2),
            "coverage_pct": round(100 * len(with_paper) / len(charges), 1) if charges else 0.0,
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
        "totals": totals(rows),
        "advice": build_advice(rows, len(duplicates), source.get("kind", "files")),
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Spend rollups the accountant asks for: by vendor, by category, by month."""
    def roll(field: str) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["kind"] != "charge":
                continue
            key = (row.get(field) or "Uncategorized").strip() or "Uncategorized"
            bucket = buckets.setdefault(key, {"key": key, "count": 0, "dollars": 0.0})
            bucket["count"] += 1
            bucket["dollars"] = round(bucket["dollars"] + row["amount"], 2)
        return sorted(buckets.values(), key=lambda item: -item["dollars"])

    by_vendor = roll("vendor")[:25]
    by_category = roll("category")[:25]
    by_account = roll("account")

    months: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["kind"] != "charge":
            continue
        key = row["date"][:7]
        bucket = months.setdefault(key, {"key": key, "count": 0, "dollars": 0.0})
        bucket["count"] += 1
        bucket["dollars"] = round(bucket["dollars"] + row["amount"], 2)

    return {
        "by_vendor": by_vendor,
        "by_category": by_category,
        "by_month": sorted(months.values(), key=lambda item: item["key"], reverse=True),
        "by_account": by_account,
    }


def build_advice(rows: list[dict[str, Any]], duplicates_dropped: int, source_kind: str = "files") -> list[dict[str, str]]:
    """Structural fixes, ranked by what they save. Each item names the evidence."""
    advice: list[dict[str, str]] = []
    by_vendor: dict[str, set[str]] = {}
    missing_by_vendor: dict[str, int] = {}
    charges = [row for row in rows if row["kind"] == "charge"]

    for row in charges:
        vendor = next((known for known in VENDOR_PORTALS if known in _norm(row["vendor"])), "")
        if vendor:
            accounts = by_vendor.setdefault(vendor, set())
            if row["account"]:
                accounts.add(row["account"].strip().lower())
            if row["status"] == "missing":
                missing_by_vendor[vendor] = missing_by_vendor.get(vendor, 0) + 1

    missing = [row for row in charges if row["status"] == "missing"]
    missing_dollars = round(sum(row["amount"] for row in missing), 2)

    if source_kind == "securo" and missing:
        advice.append(
            {
                "kind": "capture-at-source",
                "title": "Attach paper as the charge lands",
                "evidence": (
                    f"{len(missing)} of {len(charges)} charges have no attachment in Securo "
                    f"(${missing_dollars:,.2f}). Every one of those is a portal hunt next January."
                ),
                "fix": (
                    "Forward vendor receipts to your Securo inbound address, or attach while you review "
                    "the charge. Then the year-end export is already complete."
                ),
            }
        )
    elif source_kind == "files" and missing:
        advice.append(
            {
                "kind": "connect-securo",
                "title": "Stop matching paper by hand",
                "evidence": (
                    f"{len(missing)} of {len(charges)} charges have no receipt file in the folder "
                    f"(${missing_dollars:,.2f}). Filename matching cannot see a receipt that was never saved."
                ),
                "fix": (
                    "Point the plugin at a Securo instance and attach receipts to the charge. "
                    "Securo then answers 'does this charge have paper?' directly."
                ),
            }
        )

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

    personal = [row for row in charges if row["card"] and "business" not in row["card"].lower()]
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

    for vendor, count in sorted(missing_by_vendor.items(), key=lambda item: -item[1]):
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


# -- storage --------------------------------------------------------------


def load_settings() -> dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Settings safe to send to the renderer: secrets become 'is it set?' flags."""
    settings = load_settings() if settings is None else settings
    safe = {key: value for key, value in settings.items() if key not in SECRET_KEYS}
    for key in SECRET_KEYS:
        safe[f"{key}_set"] = bool(settings.get(key))
    return safe


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    current = load_settings()
    incoming = {key: value for key, value in settings.items() if key in SETTINGS_KEYS}
    if "securo_url" in incoming and incoming["securo_url"]:
        incoming["securo_url"] = securo_client.normalize_base_url(str(incoming["securo_url"]))
    for key, value in incoming.items():
        if key in SECRET_KEYS and value in ("", None):
            current.pop(key, None)
            continue
        if value in ("", None):
            current.pop(key, None)
            continue
        current[key] = value
    SETTINGS_PATH.write_text(json.dumps(current, indent=2))
    try:
        os.chmod(SETTINGS_PATH, 0o600)
    except OSError:
        pass
    return current


def configured(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the pane needs to decide between 'ready', 'set it up' and 'broken'."""
    settings = load_settings() if settings is None else settings
    has_securo = bool(settings.get("securo_url"))
    has_credential = bool(settings.get("securo_token") or (settings.get("securo_email") and settings.get("securo_password")))
    has_files = bool(settings.get("transactions_csv") or settings.get("receipts_dir"))
    return {
        "configured": bool((has_securo and has_credential) or settings.get("transactions_csv")),
        "mode": "securo" if has_securo else ("files" if has_files else "none"),
        "credentials": has_credential,
    }


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


def scan(settings: dict[str, Any] | None = None, *, client: Any = None) -> dict[str, Any]:
    settings = settings or load_settings()
    ledger = build_ledger(settings, securo_client_=client)
    save_ledger(ledger)
    return ledger


def cached_attachment(securo_id: str, attachment_id: str, filename: str) -> Path:
    """Where a downloaded Securo receipt lives: stable, per transaction, per file."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "receipt")
    return ATTACHMENT_DIR / securo_id / f"{attachment_id}-{safe}"


def safe_name(stem: str, suffix: str = "") -> str:
    """A filename an accountant can read and a filesystem will accept."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._$-]+", "_", stem or "receipt")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")[:120]
    suffix = suffix if suffix.startswith(".") or not suffix else f".{suffix}"
    if suffix and not cleaned.lower().endswith(suffix.lower()):
        cleaned = f"{cleaned}{suffix}"
    return cleaned or f"receipt{suffix}"
