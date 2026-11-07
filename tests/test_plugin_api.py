"""Route-level tests for the plugin backend.

Needs fastapi + httpx; skipped when they are absent so the engine tests still run
on a bare python.

Run: python3 -m pytest tests -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    import ledger_core

    monkeypatch.setattr(ledger_core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_core, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ledger_core, "LEDGER_PATH", tmp_path / "ledger.json")
    import plugin_api

    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/receipt-ledger")
    return TestClient(app)


def test_settings_post_scans_and_ledger_returns_rows(client: TestClient) -> None:
    response = client.post(
        "/api/plugins/receipt-ledger/settings",
        json={
            "receipts_dir": str(SAMPLES / "receipts"),
            "transactions_csv": str(SAMPLES / "transactions-2026.csv"),
        },
    )

    counts = response.json()["counts"]
    assert counts["transactions"] == 16
    assert counts["matched"] == 9
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

    assert client.get("/api/plugins/receipt-ledger/status").json()["configured"] is True


def test_package_writes_zip_with_index_and_missing_list(client: TestClient, tmp_path: Path) -> None:
    import zipfile

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
    assert "receipt-index.csv" in names
    assert "still-missing.csv" in names
    assert len([n for n in names if n.startswith("receipts/")]) == 9
