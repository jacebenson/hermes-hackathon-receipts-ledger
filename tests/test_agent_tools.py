"""Deterministic offline tool and real plugin-entry contract tests."""
import importlib
import importlib.util
import json
import shutil
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_plugin(root=ROOT, name="receipt_ledger_test_plugin"):
    spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PLUGIN = load_plugin()
tools = importlib.import_module("receipt_ledger_test_plugin.dashboard.agent_tools")


class RecordingContext:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, **kwargs):
        assert name not in self.tools
        self.tools[name] = kwargs


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.delenv("RECEIPT_LEDGER_SECURO_TOKEN", raising=False)
    monkeypatch.delenv("RECEIPT_LEDGER_SECURO_PASSWORD", raising=False)
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tools, "_managed_settings", lambda: {})
    settings = {"transactions_csv": "/not-read/charges.csv"}
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    rows = [
        {"id": "a", "date": "2026-01-01", "vendor": "Hetzner", "amount": 100.10, "category": "Hosting", "account": "Business", "kind": "charge", "status": "attached", "paper": "securo", "portal": "Hetzner billing"},
        {"id": "b", "date": "2026-02-03", "vendor": "Hetzner", "amount": 50, "category": "Hosting", "account": "Business", "kind": "charge", "status": "missing", "paper": None, "portal": "Hetzner billing"},
        {"id": "c", "date": "2026-02-01", "vendor": "Hover", "amount": 20, "category": "Domains", "account": "Personal", "kind": "charge", "status": "matched", "paper": "file", "portal": "Hover billing"},
        {"id": "d", "date": "2026-03-01", "vendor": "GitHub", "amount": 60.20, "category": "Software", "account": "Business", "kind": "charge", "status": "missing", "paper": None, "portal": "GitHub billing"},
        {"id": "credit", "date": "2026-03-02", "vendor": "Hetzner", "amount": 999, "category": "Hosting", "account": "Business", "kind": "credit", "status": "missing", "portal": "Hetzner billing"},
    ]
    ledger = {"generated_at": "2026-03-04T12:00:00", "source": {"kind": "securo", "from": "2026-01-01", "to": "2026-03-03"}, "transactions": rows}
    (tmp_path / "ledger.json").write_text(json.dumps(ledger))
    return tmp_path, ledger


def buckets(result):
    assert result["ok"], result
    return {item["key"]: (item["count"], item["dollars"]) for item in result["totals"]}


def test_spend_groupings_and_rows(saved):
    result = tools.spend()
    assert result["count"] == 4
    assert result["dollars"] == 230.30
    assert len(result["rows"]) == 4
    assert buckets(result) == {"Hetzner": (2, 150.10), "Hover": (1, 20), "GitHub": (1, 60.20)}
    assert buckets(tools.spend(by="category")) == {"Hosting": (2, 150.10), "Domains": (1, 20), "Software": (1, 60.20)}
    assert buckets(tools.spend(by="month")) == {"2026-01": (1, 100.10), "2026-02": (2, 70), "2026-03": (1, 60.20)}
    assert buckets(tools.spend(by="account")) == {"Business": (3, 210.30), "Personal": (1, 20)}


def test_filters_inclusive_and_case_insensitive(saved):
    result = tools.spend(from_date="2026-02-03", to_date="2026-02-03", vendor="HETZ", category="host")
    assert result["count"] == 1
    assert result["dollars"] == 50
    assert result["rows"][0]["id"] == "b"
    assert tools.spend(vendor="absent")["dollars"] == 0


def test_missing_amount_order_limit_portal(saved):
    result = tools.missing_paper(min_amount=50)
    assert [row["id"] for row in result["rows"]] == ["d", "b"]
    assert [row["portal"] for row in result["rows"]] == ["GitHub billing", "Hetzner billing"]
    assert result["missing_dollars"] == 110.20
    assert tools.missing_paper(min_amount=50.01)["total_count"] == 1
    limited = tools.missing_paper(limit=1)
    assert limited["count"] == 1 and limited["total_count"] == 2 and limited["has_more"]


def test_coverage(saved):
    result = tools.receipt_coverage()
    assert {k: result[k] for k in ("charges", "with_paper", "missing", "missing_dollars", "coverage_pct")} == {"charges": 4, "with_paper": 2, "missing": 2, "missing_dollars": 110.20, "coverage_pct": 50.0}
    assert tools.receipt_coverage(from_date="2026-02-01", to_date="2026-02-03")["coverage_pct"] == 50.0
    empty = tools.receipt_coverage(from_date="2027-01-01")
    assert empty["charges"] == 0 and empty["coverage_pct"] == 0


def test_status_and_managed_settings(saved, monkeypatch):
    result = tools.status()
    assert result["configured"] and result["source"] == "securo"
    assert result["period"] == {"from": "2026-01-01", "to": "2026-03-03"}
    assert result["last_scan"] == "2026-03-04T12:00:00"
    monkeypatch.setattr(tools, "_managed_settings", lambda: {"securo_url": "https://unused.invalid", "securo_token": "SECRET"})
    assert tools.status()["ok"]
    assert "SECRET" not in json.dumps(tools.status())


@pytest.mark.parametrize("function", [tools.spend, tools.missing_paper, tools.receipt_coverage, tools.status])
def test_not_configured_and_no_scan(saved, function):
    path, _ = saved
    # A saved scan outlives its configuration: the numbers are still evidence, so
    # the tools answer and say the connection is not configured right now.
    (path / "settings.json").unlink()
    result = function()
    assert result["ok"] is True
    assert result["configured"] is False
    assert result["last_scan"]

    # With neither configuration nor a scan there is nothing true to say.
    (path / "ledger.json").unlink()
    result = function()
    assert result["ok"] is False and "Configure" in result["summary"]
    assert "dollars" not in result and "charges" not in result

    # Configured but never scanned explains the scan, not the configuration.
    (path / "settings.json").write_text('{"transactions_csv": "unused.csv"}')
    result = function()
    assert result["ok"] is False and "scan" in result["summary"]


@pytest.mark.parametrize("call", [lambda: tools.spend(by="bad"), lambda: tools.spend(from_date="wrong"), lambda: tools.spend(from_date="2026-03-01", to_date="2026-01-01"), lambda: tools.missing_paper(min_amount=-1), lambda: tools.missing_paper(limit=0), lambda: tools.missing_paper(min_amount="NaN")])
def test_bad_arguments_do_not_raise(saved, call):
    result = call()
    assert result["ok"] is False and result["summary"]


def test_corrupt_scan_does_not_invent_numbers(saved):
    path, _ = saved
    (path / "ledger.json").write_text('{broken')
    assert tools.spend()["ok"] is False
    (path / "ledger.json").write_text('{"transactions": []}')
    assert tools.receipt_coverage()["ok"] is False


def test_no_writes_or_network(saved, monkeypatch):
    path, _ = saved
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    def forbidden(*args, **kwargs):
        pytest.fail("read-only tool attempted a write or network connection")
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for function in (tools.spend, tools.missing_paper, tools.receipt_coverage, tools.status):
        assert function()["ok"]
    assert before == {p.name: p.read_bytes() for p in path.iterdir()}


def test_registration_schemas_and_runtime_handlers(saved):
    ctx = RecordingContext()
    PLUGIN.register(ctx)
    assert set(ctx.tools) == {"receipt_ledger_spend", "receipt_ledger_missing_paper", "receipt_ledger_coverage", "receipt_ledger_status"}
    for name, registration in ctx.tools.items():
        schema = registration["schema"]
        assert schema["name"] == name
        assert schema["description"] and registration["description"]
        assert registration["toolset"] == "receipt_ledger"
        params = schema["parameters"]
        assert params["type"] == "object" and params["additionalProperties"] is False
        assert isinstance(params["required"], list)
        assert set(params["required"]) <= set(params["properties"])
        assert params["required"] == []  # All four signatures have defaults.
        for prop in params["properties"].values():
            assert prop["type"] in ("string", "number", "integer") and prop["description"]
        handler = registration["handler"]
        assert callable(handler)
        result = handler({}, task_id="runtime-task")
        assert isinstance(result, dict) and result["ok"] and result["summary"]
    result = ctx.tools["receipt_ledger_spend"]["handler"]({"by": "month", "category": "hosting"})
    assert result["dollars"] == 150.10


def test_missing_dashboard_degrades(tmp_path):
    shutil.copyfile(ROOT / "__init__.py", tmp_path / "__init__.py")
    module = load_plugin(tmp_path, "receipt_ledger_incomplete")
    ctx = RecordingContext()
    module.register(ctx)
    assert len(ctx.tools) == 4
    for registration in ctx.tools.values():
        result = registration["handler"]({})
        assert result["ok"] is False and "dashboard" in result["summary"]
