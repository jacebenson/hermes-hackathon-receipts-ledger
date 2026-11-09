"""The data boundary rejects unsourced rules before a consumer can use them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_jurisdictions import validate_bundle, validate_record


@pytest.fixture
def record():
    return {
        "schema_version": 1, "id": "us-mn", "name": "Minnesota",
        "level": "state", "confidence": "verified", "updated_on": "2026-11-09",
        "sources": {key: {"url": "https://www.sos.mn.gov/", "title": "Official source",
                            "checked_on": "2026-11-09", "status": "ok"}
                    for key in ("revenue", "registry", "withholding")},
        "obligations": [{"id": "mn-example", "title": "Example", "applies_when": "LLC",
                         "form": "Annual renewal", "due_rule": "See source", "frequency": "annual",
                         "source_url": "https://www.sos.mn.gov/", "quote": "Example fixture quote.",
                         "verified_on": "2026-11-09"}],
        "notes": "Fixture only, not a tax rule.",
    }


def test_shipped_bundle_validates():
    assert validate_bundle(ROOT / "jurisdictions")["errors"] == []


@pytest.mark.parametrize("change, expected", [
    (lambda r: r["obligations"][0].pop("quote"), "quote"),
    (lambda r: r["sources"]["revenue"].update(url="http://example.gov/"), "https"),
    (lambda r: r.update(updated_on="2026-02-30"), "date"),
    (lambda r: r.update(id="us-wi"), "filename"),
    (lambda r: r.update(confidence="unknown"), "unknown"),
])
def test_invalid_records_are_rejected(record, change, expected):
    change(record)
    assert any(expected in error for error in validate_record(record, "us-mn.json"))


def test_valid_inline_record(record):
    assert validate_record(record, "us-mn.json") == []


@pytest.mark.parametrize("change, expected", [
    (lambda r: r.update(schema_version=True), "schema_version"),
    (lambda r: r.update(sources=[]), "sources"),
    (lambda r: r.update(obligations={}), "array"),
    (lambda r: r["obligations"].append(None), "object"),
    (lambda r: r["sources"].update(revenue=None), "object"),
    (lambda r: r["sources"]["registry"].update(status="unchecked"), "verified"),
    (lambda r: r.update(obligations=[]), "at least one"),
    (lambda r: r["obligations"][0].update(due_rule=""), "due_rule"),
    (lambda r: r["obligations"][0].update(verified_on="20261109"), "date"),
    (lambda r: r.update(confidence="seeded"), "seeded"),
])
def test_nested_contract_failures_do_not_crash(record, change, expected):
    change(record)
    assert any(expected in error for error in validate_record(record, "us-mn.json"))


def test_bundle_requires_exact_roster(tmp_path):
    (tmp_path / "us-example.json").write_text("{}", encoding="utf-8")
    errors = validate_bundle(tmp_path)["errors"]
    assert "missing us-federal.json" in errors
    assert "missing us-dc.json" in errors
    assert "unexpected us-example.json" in errors


def test_source_checker_deduplicates_obligation_and_source_urls(record):
    from verify_jurisdiction_sources import collect_urls
    assert collect_urls([record]) == ["https://www.sos.mn.gov/"]


def test_source_updates_never_promote_confidence_or_change_quotes(record):
    from verify_jurisdiction_sources import update_sources
    record["confidence"] = "unknown"
    before = record["obligations"][0].copy()
    update_sources(record, {"https://www.sos.mn.gov/": {"status": "blocked"}}, "2026-11-10")
    assert record["confidence"] == "unknown"
    assert record["obligations"][0] == before
    assert record["sources"]["registry"]["status"] == "blocked"
    assert record["sources"]["registry"]["checked_on"] == "2026-11-10"


def test_source_checker_default_is_read_only(tmp_path, monkeypatch, capsys, record):
    import json
    import verify_jurisdiction_sources as checker
    path = tmp_path / "us-mn.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    original = path.read_bytes()
    monkeypatch.setattr(checker, "check_url", lambda url, timeout: {
        "url": url, "code": 403, "status": "blocked", "error": "Forbidden"})
    monkeypatch.setattr(sys, "argv", ["verify", "--directory", str(tmp_path)])
    assert checker.main() == 1
    assert path.read_bytes() == original
    assert "1 HTTP responses; 0 ok; 1 blocked; 0 unconfirmed" in capsys.readouterr().out


def test_source_checker_write_updates_exact_file(tmp_path, monkeypatch, record):
    import json
    import verify_jurisdiction_sources as checker
    path = tmp_path / "us-mn.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(checker, "check_url", lambda url, timeout: {
        "url": url, "code": 200, "status": "ok", "error": ""})
    monkeypatch.setattr(sys, "argv", ["verify", "--directory", str(tmp_path), "--write"])
    assert checker.main() == 0
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["sources"]["registry"]["checked_on"] == checker.date.today().isoformat()
    assert written["obligations"] == record["obligations"]
    assert written["confidence"] == "verified"
