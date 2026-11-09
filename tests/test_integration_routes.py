"""The routes that tie the new pieces together.

These tests run the real plugin API over a ledger fixture: the jurisdiction
bundle, the audit journal, and the one path that turns a human classification
decision into a standing rule without touching Securo.

Run: .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

PREFIX = "/api/plugins/receipt-ledger"

LEDGER = {
    "generated_at": "2026-11-09T12:00:00",
    "source": {"kind": "securo", "securo_url": "http://securo.home", "from": "2026-01-01", "to": "2026-11-09"},
    "counts": {"charges": 2, "with_paper": 1, "missing": 1, "missing_dollars": 50.0, "coverage_pct": 50.0},
    "transactions": [
        {"id": "securo:a", "securo_id": "a", "date": "2026-08-27", "vendor": "Hetzner", "amount": 63.44,
         "account": "Business Checking", "card": "Business Visa 0054", "category": "Hosting",
         "kind": "charge", "status": "attached", "attachment_count": 1, "source": "securo"},
        {"id": "securo:b", "securo_id": "b", "date": "2026-08-19", "vendor": "Fireworks AI", "amount": 50.0,
         "account": "Personal Visa", "card": "Personal 8830", "category": "AI",
         "kind": "charge", "status": "missing", "attachment_count": 0, "source": "securo"},
    ],
    "missing": [{"date": "2026-08-19", "vendor": "Fireworks AI", "amount": 50.0, "portal": ""}],
    "totals": {"by_vendor": [], "by_category": [], "by_month": [], "by_account": []},
    "advice": [],
}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    import audit
    import ledger_core

    monkeypatch.setattr(ledger_core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ledger_core, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(ledger_core, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(ledger_core, "ATTACHMENT_DIR", tmp_path / "attachments")
    (tmp_path / "ledger.json").write_text(json.dumps(LEDGER))
    import plugin_api

    app = FastAPI()
    app.include_router(plugin_api.router, prefix=PREFIX)
    client = TestClient(app)
    client.tmp = tmp_path  # type: ignore[attr-defined]
    return client


def test_the_bundle_is_queryable_and_carries_its_confidence(client: TestClient) -> None:
    data = client.get(f"{PREFIX}/jurisdictions").json()

    counts = data["counts"]
    assert counts["verified"] == 3            # read and quoted by a person
    assert counts["extracted"] >= 30          # quoted from a live page, re-checked
    assert counts["seeded"] + counts["unknown"] <= 12
    assert sum(counts.values()) == 52
    assert "could not be reached" in data["statement"]
    assert data["records"][0]["level"] == "federal"


def test_a_verified_record_keeps_its_quotes_and_parses_plain_due_dates(client: TestClient) -> None:
    record = client.get(f"{PREFIX}/jurisdictions", params={"state_id": "us-mn"}).json()["record"]

    assert record["confidence"] == "verified"
    renewal = record["obligations"][0]
    assert renewal["due_rule"] == "December 31"
    assert renewal["next_due"].endswith("-12-31")
    assert "termination or revocation" in renewal["quote"]
    assert renewal["source_url"].startswith("https://www.sos.mn.gov/")


def test_a_rule_from_the_owner_reclassifies_without_a_network_call(client: TestClient, monkeypatch) -> None:
    import ledger_core

    def no_network(*args, **kwargs):
        raise AssertionError("saving a rule must not trigger a Securo fetch")

    monkeypatch.setattr(ledger_core, "scan", no_network)

    response = client.post(
        f"{PREFIX}/classification/rule",
        json={
            "decision": "business",
            "match": {"vendor_contains": "Hetzner"},
            "note": "Hosting is business",
            "created_by": "owner",
        },
    ).json()

    assert response["ok"] is True
    ledger = json.loads((client.tmp / "ledger.json").read_text())  # type: ignore[attr-defined]
    row = next(item for item in ledger["transactions"] if item["vendor"] == "Hetzner")
    assert row["classification"]["decision"] == "business"
    assert row["classification"]["source"] == "rule"
    assert ledger["classification_summary"]["business_dollars"] == 63.44
    assert ledger["classification_summary"]["unreviewed_dollars"] == 50.0


def test_a_rule_needs_something_to_match_on(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/classification/rule", json={"decision": "business", "match": {}}).json()

    assert response["ok"] is False
    assert "match" in response["error"]


def test_an_invalid_decision_is_refused(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/classification/rule",
        json={"decision": "deductible", "match": {"vendor_contains": "Hetzner"}},
    ).json()

    assert response["ok"] is False
    assert "decision" in response["error"].lower()


def test_the_journal_states_what_the_plugin_will_not_do(client: TestClient) -> None:
    data = client.get(f"{PREFIX}/audit").json()

    titles = {item["title"] for item in data["limits"]}
    assert "Not tax advice" in titles
    assert "Reads and writes are visible" in titles
    assert data["recent"] == []


def test_saving_a_rule_is_journalled(client: TestClient) -> None:
    client.post(
        f"{PREFIX}/classification/rule",
        json={"decision": "personal", "match": {"vendor_contains": "Fireworks"}},
    )

    entries = client.get(f"{PREFIX}/audit").json()["recent"]
    assert any(entry["action"] == "settings" and "rule" in entry["detail"] for entry in entries)
    assert client.post(f"{PREFIX}/audit/clear").json()["ok"] is True
    assert client.get(f"{PREFIX}/audit").json()["recent"] == []
