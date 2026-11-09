"""Route-level tests for the plugin backend.

Needs fastapi + httpx; skipped when they are absent so the engine tests still run
on a bare python.

Run: python3 -m pytest tests -q
"""

from __future__ import annotations

import csv
import json
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class FakeSecuro:
    """A client that answers like Securo without touching the network."""

    base_url = "http://securo.home"

    def __init__(self, transactions: list[dict] | None = None) -> None:
        self._transactions = transactions or [
            {
                "id": "t1",
                "date": "2026-08-27",
                "description": "Hetzner Online GmbH",
                "payee_name": "Hetzner",
                "amount": 63.44,
                "amount_primary": 63.44,
                "type": "debit",
                "account_id": "acct-1",
                "attachment_count": 1,
                "category": {"name": "Hosting"},
            },
            {
                "id": "t2",
                "date": "2026-08-17",
                "description": "Hover",
                "amount": 19.19,
                "type": "debit",
                "account_id": "acct-1",
                "attachment_count": 1,
                "category": {"name": "Domains"},
            },
            {
                "id": "t3",
                "date": "2026-08-19",
                "description": "Fireworks AI",
                "amount": 50.00,
                "type": "debit",
                "account_id": "acct-1",
                "attachment_count": 0,
                "category": {"name": "AI"},
            },
        ]

    def accounts(self) -> list[dict]:
        return [{"id": "acct-1", "name": "Business Checking", "type": "checking", "institution": "US Bank", "masked_number": "", "currency": "USD"}]

    def transactions(self, from_date=None, to_date=None) -> list[dict]:
        return self._transactions

    def attachments(self, transaction_id: str) -> list[dict]:
        return [{"id": f"att-{transaction_id}", "filename": f"{transaction_id}-receipt.pdf", "content_type": "application/pdf", "size": 18}]

    def download_attachment(self, transaction_id: str, attachment_id: str) -> tuple[str, str, bytes]:
        return (f"{transaction_id}-receipt.pdf", "application/pdf", b"%PDF-1.4 " + transaction_id.encode())


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    import ledger_core

    monkeypatch.setattr(ledger_core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_core, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ledger_core, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(ledger_core, "ATTACHMENT_DIR", tmp_path / "attachments")
    import plugin_api

    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/receipt-ledger")
    return TestClient(app)


@pytest.fixture()
def fake_securo(monkeypatch) -> FakeSecuro:
    import securo_client

    fake = FakeSecuro()
    monkeypatch.setattr(securo_client, "client_for", lambda settings, **overrides: fake)
    return fake


# -- files mode (no Securo) ----------------------------------------------


def test_settings_post_scans_and_ledger_returns_rows(client: TestClient) -> None:
    response = client.post(
        "/api/plugins/receipt-ledger/settings",
        json={
            "receipts_dir": str(SAMPLES / "receipts"),
            "transactions_csv": str(SAMPLES / "transactions-2026.csv"),
        },
    ).json()

    counts = response["ledger"]["counts"]
    assert counts["transactions"] == 16
    assert counts["matched"] == 9
    assert counts["missing"] == 7
    assert counts["duplicates_dropped"] == 1

    ledger = client.get("/api/plugins/receipt-ledger/ledger").json()
    assert len(ledger["transactions"]) == 16
    assert any(item["kind"] == "split-vendor" for item in ledger["advice"])


def test_status_reports_configured_after_settings(client: TestClient) -> None:
    assert client.get("/api/plugins/receipt-ledger/status").json()["configured"] is False

    client.post(
        "/api/plugins/receipt-ledger/settings",
        json={
            "receipts_dir": str(SAMPLES / "receipts"),
            "transactions_csv": str(SAMPLES / "transactions-2026.csv"),
        },
    )

    status = client.get("/api/plugins/receipt-ledger/status").json()
    assert status["configured"] is True
    assert status["mode"] == "files"


def test_package_writes_zip_with_index_and_missing_list(client: TestClient, tmp_path: Path) -> None:
    client.post(
        "/api/plugins/receipt-ledger/settings",
        json={
            "receipts_dir": str(SAMPLES / "receipts"),
            "transactions_csv": str(SAMPLES / "transactions-2026.csv"),
        },
    )
    result = client.post(
        "/api/plugins/receipt-ledger/package", json={"output_dir": str(tmp_path / "pack")}
    ).json()

    assert result["receipts"] == 9
    assert result["still_missing"] == 7
    with zipfile.ZipFile(result["archive"]) as bundle:
        names = bundle.namelist()
        readme = bundle.read("README.txt").decode()
        totals = bundle.read("totals.csv").decode()
    assert "receipt-index.csv" in names
    assert "still-missing.csv" in names
    assert "totals.csv" in names
    assert "Receipt ledger package" in readme
    assert "by_vendor" in totals
    assert len([name for name in names if name.startswith("receipts/")]) == 9


# -- Securo mode ---------------------------------------------------------


def test_securo_settings_scan_marks_attached_and_missing(client: TestClient, fake_securo: FakeSecuro) -> None:
    payload = client.post(
        "/api/plugins/receipt-ledger/settings",
        json={
            "securo_url": "securo.home",
            "securo_email": "owner@example.com",
            "securo_password": "secret",
        },
    ).json()

    ledger = payload["ledger"]
    assert payload["mode"] == "securo"
    assert ledger["source"]["kind"] == "securo"
    assert ledger["source"]["securo_url"] == "http://securo.home"
    assert ledger["counts"]["charges"] == 3
    assert ledger["counts"]["attached"] == 2
    assert ledger["counts"]["missing"] == 1
    assert ledger["counts"]["coverage_pct"] == 66.7
    assert ledger["missing"][0]["vendor"] == "Fireworks AI"
    assert any(item["kind"] == "capture-at-source" for item in ledger["advice"])


def test_settings_never_echo_a_secret(client: TestClient, fake_securo: FakeSecuro, tmp_path: Path) -> None:
    client.post(
        "/api/plugins/receipt-ledger/settings",
        json={"securo_url": "securo.home", "securo_email": "owner@example.com", "securo_password": "hunter2"},
    )

    settings = client.get("/api/plugins/receipt-ledger/settings").json()
    assert "securo_password" not in settings
    assert settings["securo_password_set"] is True
    assert json.loads((tmp_path / "settings.json").read_text())["securo_password"] == "hunter2"
    assert oct((tmp_path / "settings.json").stat().st_mode)[-3:] == "600"


def test_package_downloads_securo_attachments(client: TestClient, fake_securo: FakeSecuro, tmp_path: Path) -> None:
    client.post(
        "/api/plugins/receipt-ledger/settings",
        json={"securo_url": "securo.home", "securo_token": "sct_test"},
    )
    result = client.post(
        "/api/plugins/receipt-ledger/package", json={"output_dir": str(tmp_path / "pack")}
    ).json()

    assert result["receipts"] == 2
    assert result["still_missing"] == 1
    assert result["errors"] == []
    with zipfile.ZipFile(result["archive"]) as bundle:
        names = bundle.namelist()
        index = list(csv.DictReader(bundle.read("receipt-index.csv").decode().splitlines()))
    receipt_files = [name for name in names if name.startswith("receipts/")]
    assert len(receipt_files) == 2
    assert all(name.endswith(".pdf") for name in receipt_files)
    assert {row["source"] for row in index} == {"securo"}
    assert {row["vendor"] for row in index} == {"Hetzner", "Hover"}


def test_scan_failure_keeps_the_last_good_ledger(client: TestClient, monkeypatch) -> None:
    from securo_client import SecuroError

    def boom(settings, **overrides):
        raise SecuroError("Securo rejected the credential.", status=401, hint="Sign in again.")

    import securo_client

    monkeypatch.setattr(securo_client, "client_for", boom)
    client.post("/api/plugins/receipt-ledger/settings", json={"transactions_csv": str(SAMPLES / "transactions-2026.csv")})
    client.post(
        "/api/plugins/receipt-ledger/settings",
        json={"securo_url": "securo.home", "securo_token": "sct_bad"},
    )

    response = client.post("/api/plugins/receipt-ledger/scan")
    assert response.status_code == 502
    body = response.json()
    assert "rejected" in body["error"]
    assert body["hint"] == "Sign in again."
    assert body["ledger"]["counts"]["transactions"] == 16  # the cached scan survived


def test_connection_test_reports_a_bad_host(client: TestClient) -> None:
    result = client.post(
        "/api/plugins/receipt-ledger/securo/test",
        json={"securo_url": "http://127.0.0.1:1", "securo_token": "sct_x"},
    ).json()

    assert result["ok"] is False
    assert "127.0.0.1:1" in result["error"]
