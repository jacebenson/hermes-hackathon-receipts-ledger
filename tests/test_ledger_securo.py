"""Securo-mode behaviour for the ledger engine.

No network: a duck-typed client stands in for Securo, so the mapping, matching
and advice rules are tested directly.

Run: python3 -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import ledger_core as core  # noqa: E402


class FakeClient:
    base_url = "http://securo.home"

    def __init__(self, rows: list[dict], accounts: list[dict] | None = None) -> None:
        self.rows = rows
        self._accounts = accounts or [
            {"id": "acct-1", "name": "Business Checking", "type": "checking", "institution": "US Bank", "masked_number": "", "currency": "USD"},
            {"id": "acct-2", "name": "Personal Visa", "type": "credit", "institution": "Chase", "masked_number": "8830", "currency": "USD"},
        ]

    def accounts(self) -> list[dict]:
        return self._accounts

    def transactions(self, from_date=None, to_date=None) -> list[dict]:
        return self.rows


def row(**kwargs) -> dict:
    base = {
        "id": "t-1",
        "date": "2026-08-27",
        "description": "Hetzner Online GmbH",
        "amount": 63.44,
        "amount_primary": None,
        "type": "debit",
        "account_id": "acct-1",
        "attachment_count": 0,
        "category": {"id": "c1", "name": "Hosting"},
        "payee_name": None,
        "currency": "USD",
        "notes": None,
    }
    base.update(kwargs)
    return base


def settings_with_securo(**extra) -> dict:
    return {"securo_url": "http://securo.home", "securo_token": "sct_test", "from_date": "2026-01-01", "to_date": "2026-12-31", **extra}


def test_securo_row_maps_to_the_ledger_shape() -> None:
    mapped = core.map_securo_transaction(
        row(payee_name="Hetzner", amount="63.44", amount_primary="63.44", attachment_count=2),
        {"acct-1": {"id": "acct-1", "name": "Business Checking", "masked_number": "", "institution": "US Bank"}},
    )

    assert mapped is not None
    assert mapped["id"] == "securo:t-1"
    assert mapped["vendor"] == "Hetzner"
    assert mapped["amount"] == pytest.approx(63.44)
    assert mapped["account"] == "Business Checking"
    assert mapped["category"] == "Hosting"
    assert mapped["attachment_count"] == 2
    assert mapped["kind"] == "charge"
    assert mapped["source"] == "securo"


def test_amount_primary_wins_when_the_currency_differs() -> None:
    mapped = core.map_securo_transaction(row(amount="1000.00", amount_primary="217.40"), {})
    assert mapped is not None
    assert mapped["amount"] == pytest.approx(217.40)


def test_credit_rows_are_not_counted_as_missing_paper() -> None:
    mapped = core.map_securo_transaction(row(type="credit", amount="-20.00"), {})
    assert mapped is not None
    assert mapped["kind"] == "credit"


def test_ledger_counts_attached_charges_as_paper(tmp_path: Path) -> None:
    client = FakeClient(
        [
            row(id="t-1", attachment_count=1),
            row(id="t-2", date="2026-08-19", description="Fireworks AI", amount="50.00"),
            row(id="t-3", date="2026-08-17", description="Hover", amount="19.19", attachment_count=1),
        ]
    )

    ledger = core.build_ledger(settings_with_securo(), securo_client_=client)

    assert ledger["counts"]["charges"] == 3
    assert ledger["counts"]["attached"] == 2
    assert ledger["counts"]["missing"] == 1
    assert ledger["counts"]["missing_dollars"] == pytest.approx(50.00)
    assert ledger["counts"]["coverage_pct"] == pytest.approx(66.7)
    assert ledger["missing"] == [
        {"date": "2026-08-19", "vendor": "Fireworks AI", "amount": pytest.approx(50.00), "portal": "fireworks.ai -> Settings -> Billing -> Invoices"}
    ]


def test_advice_tells_you_to_capture_where_the_charge_lives(tmp_path: Path) -> None:
    client = FakeClient([row(id="t-1"), row(id="t-2", attachment_count=0)])

    ledger = core.build_ledger(settings_with_securo(), securo_client_=client)
    kinds = {item["kind"] for item in ledger["advice"]}

    assert "capture-at-source" in kinds
    assert "connect-securo" not in kinds


def test_folder_receipts_still_fill_securo_gaps(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "Fireworks_2026-08-19 $50.00.pdf").write_text("paid")
    client = FakeClient([row(id="t-1", date="2026-08-19", description="Fireworks AI", amount="50.00")])

    ledger = core.build_ledger(settings_with_securo(receipts_dir=str(receipts)), securo_client_=client)

    assert ledger["transactions"][0]["status"] == "matched"
    assert ledger["transactions"][0]["paper"] == "file"
    assert ledger["counts"]["missing"] == 0


def test_csv_rows_not_in_securo_are_merged_not_dropped(tmp_path: Path) -> None:
    csv_path = tmp_path / "txns.csv"
    csv_path.write_text(
        "date,vendor,amount,account,card\n2026-08-27,Hetzner Online GmbH,63.44,biz,Business Visa\n2026-05-01,Old Vendor,12.00,biz,Business Visa\n"
    )
    client = FakeClient([row(id="t-1")])

    ledger = core.build_ledger(settings_with_securo(transactions_csv=str(csv_path)), securo_client_=client)

    assert ledger["source"]["csv_added"] == 1
    assert any(item["vendor"] == "Old Vendor" for item in ledger["transactions"])


def test_totals_roll_up_by_vendor_category_month_and_account(tmp_path: Path) -> None:
    client = FakeClient(
        [
            row(id="t-1", amount="63.44"),
            row(id="t-2", date="2026-08-27", amount="36.56"),
            row(id="t-3", date="2026-07-01", description="Hover", amount="19.19", category={"name": "Domains"}),
            row(id="t-4", type="credit", amount="-10.00"),
        ]
    )

    totals = core.build_ledger(settings_with_securo(), securo_client_=client)["totals"]

    assert totals["by_vendor"][0] == {"key": "Hetzner Online GmbH", "count": 2, "dollars": pytest.approx(100.00)}
    assert totals["by_category"][0]["key"] == "Hosting"
    assert totals["by_month"] == [
        {"key": "2026-08", "count": 2, "dollars": pytest.approx(100.00)},
        {"key": "2026-07", "count": 1, "dollars": pytest.approx(19.19)},
    ]
    assert totals["by_account"][0]["key"] == "Business Checking"


def test_configured_requires_a_credential_for_securo() -> None:
    assert core.configured({"securo_url": "http://securo.home"})["configured"] is False
    assert core.configured({"securo_url": "http://securo.home", "securo_token": "sct_x"})["configured"] is True
    assert core.configured({"securo_url": "http://securo.home", "securo_email": "a@b.c", "securo_password": "x"})["configured"] is True
    assert core.configured({"transactions_csv": "/tmp/x.csv"})["mode"] == "files"


def test_safe_name_keeps_a_readable_filename() -> None:
    assert core.safe_name("2026-08-27 Hetzner Online GmbH $63.44", ".pdf") == "2026-08-27 Hetzner Online GmbH $63.44.pdf"
    escaped = core.safe_name("../../etc/passwd", ".pdf")
    assert "/" not in escaped and ".." not in escaped
    assert core.safe_name("", ".pdf") == "receipt.pdf"
