"""Behavioural tests for the receipt matching engine.

Run: python3 -m pytest tests -q   (from the repo root, with dashboard/ on the path)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import ledger_core as core  # noqa: E402


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "vendor", "amount", "account", "card"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_duplicate_bytes_are_dropped(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "Hetzner_2026-09-27 $63.44.pdf").write_text("same bytes")
    (receipts / "copy of Hetzner $63.44.pdf").write_text("same bytes")

    kept, duplicates = core.scan_receipts(receipts)

    assert len(kept) == 1
    assert len(duplicates) == 1
    assert duplicates[0].duplicate_of == kept[0].path


def test_receipt_matches_its_own_month_not_a_later_one(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "Hetzner_2026-09-27 $63.44.pdf").write_text("september")
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [
            {"date": "2026-10-27", "vendor": "Hetzner Online GmbH", "amount": "63.44", "account": "biz", "card": "Business Visa"},
            {"date": "2026-09-27", "vendor": "Hetzner Online GmbH", "amount": "63.44", "account": "biz", "card": "Business Visa"},
        ],
    )

    ledger = core.build_ledger({"receipts_dir": str(receipts), "transactions_csv": str(csv_path)})
    by_date = {row["date"]: row for row in ledger["transactions"]}

    assert by_date["2026-09-27"]["status"] == "matched"
    assert by_date["2026-10-27"]["status"] == "missing"


def test_unique_amount_matches_without_a_date_in_the_filename(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "FounderStack-Invoice-0001 $499.00.pdf").write_text("no date here")
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [{"date": "2026-09-01", "vendor": "FounderStack", "amount": "499.00", "account": "biz", "card": "Business Visa"}],
    )

    ledger = core.build_ledger({"receipts_dir": str(receipts), "transactions_csv": str(csv_path)})

    assert ledger["transactions"][0]["status"] == "matched"


def test_missing_line_carries_the_portal_to_go_get_it(tmp_path: Path) -> None:
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [{"date": "2026-10-27", "vendor": "Hetzner Online GmbH", "amount": "63.44", "account": "biz", "card": "Business Visa"}],
    )

    ledger = core.build_ledger({"receipts_dir": str(tmp_path / "none"), "transactions_csv": str(csv_path)})

    assert ledger["missing"][0]["vendor"].startswith("Hetzner")
    assert "console.hetzner.cloud" in ledger["missing"][0]["portal"]


def test_advice_flags_a_vendor_split_across_two_accounts(tmp_path: Path) -> None:
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [
            {"date": "2026-08-17", "vendor": "Hover", "amount": "19.19", "account": "US Bank Business", "card": "Business Visa"},
            {"date": "2026-07-18", "vendor": "Hover", "amount": "74.99", "account": "Personal Visa", "card": "Personal Visa"},
        ],
    )

    ledger = core.build_ledger({"receipts_dir": str(tmp_path / "none"), "transactions_csv": str(csv_path)})
    kinds = {item["kind"] for item in ledger["advice"]}

    assert "split-vendor" in kinds
    assert any("hover" in item["evidence"].lower() for item in ledger["advice"])


def test_advice_flags_personal_card_spend_as_owner_reimbursement(tmp_path: Path) -> None:
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [{"date": "2026-09-22", "vendor": "OpenAI ChatGPT Plus", "amount": "20.00", "account": "Personal Visa", "card": "Personal Visa 8830"}],
    )

    ledger = core.build_ledger({"receipts_dir": str(tmp_path / "none"), "transactions_csv": str(csv_path)})

    assert any(item["kind"] == "owner-reimbursement" for item in ledger["advice"])


def test_unreadable_receipt_is_not_silently_counted_as_matched(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "random-scan $999.00.png").write_text("nothing to do with the ledger")
    csv_path = write_csv(
        tmp_path / "txns.csv",
        [{"date": "2026-08-10", "vendor": "RingCentral", "amount": "1069.20", "account": "biz", "card": "Business Visa"}],
    )

    ledger = core.build_ledger({"receipts_dir": str(receipts), "transactions_csv": str(csv_path)})

    assert ledger["transactions"][0]["status"] == "missing"
    assert ledger["unmatched_receipts"][0]["filename"] == "random-scan $999.00.png"
