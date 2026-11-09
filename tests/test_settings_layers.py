"""Where settings come from: Hermes config, .env secrets, and the plugin's file.

The rule under test: Hermes' `plugins.entries.receipt-ledger.settings` (written by
Settings -> Plugins, the CLI, or ansible) is the managed layer and wins over the
pane's own `data/settings.json`. `type: secret` fields never live in config.yaml;
they are read from .env, so a credential is set once and never re-typed.

Run: python3 -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import ledger_core as core  # noqa: E402


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch) -> Path:
    """A throwaway data dir, a throwaway HERMES_HOME, and no real Hermes config."""
    monkeypatch.setattr(core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(core, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(core, "LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(core, "ATTACHMENT_DIR", tmp_path / "attachments")
    hermes_home = tmp_path / "hermes-home"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for name in core.SECRET_ENV.values():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(core, "_hermes_config_settings", lambda: {})
    return hermes_home


def write_plugin_settings(tmp_path: Path, data: dict) -> None:
    (tmp_path / "settings.json").write_text(json.dumps(data))


def test_secret_can_come_from_the_environment(isolated: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECEIPT_LEDGER_SECURO_PASSWORD", "hunter2")

    settings = core.hermes_settings()

    assert settings["securo_password"] == "hunter2"


def test_dotenv_is_read_when_the_process_env_is_empty(isolated: Path) -> None:
    (isolated / ".env").write_text(
        "SECRET_KEY=unrelated\nRECEIPT_LEDGER_SECURO_PASSWORD='from-dotenv'\n",
        encoding="utf-8",
    )

    assert core._dotenv_value("RECEIPT_LEDGER_SECURO_PASSWORD") == "from-dotenv"
    assert core._dotenv_value("NOT_THERE") == ""
    assert core.hermes_settings()["securo_password"] == "from-dotenv"


def test_config_yaml_layer_is_read_from_plugins_entries(isolated: Path, monkeypatch) -> None:
    """The shape Settings -> Plugins writes: plugins.entries.<id>.settings.<key>."""
    config = {"plugins": {"entries": {"receipt-ledger": {"settings": {"securo_url": "http://config.home", "from_date": ""}}}}}
    monkeypatch.setattr(core, "_hermes_config_settings", lambda: {
        key: value for key, value in config["plugins"]["entries"]["receipt-ledger"]["settings"].items() if value
    })

    assert core.hermes_settings() == {"securo_url": "http://config.home"}


def test_hermes_config_wins_over_the_plugin_file(isolated: Path, monkeypatch, tmp_path: Path) -> None:
    write_plugin_settings(tmp_path, {"securo_url": "http://old.home", "from_date": "2025-01-01"})
    monkeypatch.setattr(core, "hermes_settings", lambda: {"securo_url": "http://config.home"})

    effective = core.effective_settings()

    assert effective["securo_url"] == "http://config.home"
    assert effective["from_date"] == "2025-01-01"  # still falls through to the file
    assert core.settings_sources()["securo_url"] == "hermes"
    assert core.settings_sources()["from_date"] == "plugin"


def test_public_settings_never_carries_a_secret(isolated: Path, monkeypatch, tmp_path: Path) -> None:
    write_plugin_settings(tmp_path, {"securo_url": "http://securo.home"})
    monkeypatch.setattr(core, "hermes_settings", lambda: {"securo_password": "hunter2", "securo_email": "owner@example.com"})

    public = core.public_settings()

    assert "securo_password" not in public
    assert public["securo_password_set"] is True
    assert public["sources"]["securo_password"] == "hermes"


def test_configured_accepts_credentials_from_config_only(isolated: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        core,
        "hermes_settings",
        lambda: {"securo_url": "http://securo.home", "securo_email": "owner@example.com", "securo_password": "hunter2"},
    )

    state = core.configured()

    assert state["configured"] is True
    assert state["mode"] == "securo"
    assert state["credentials"] is True


def test_scan_uses_the_effective_settings(isolated: Path, monkeypatch) -> None:
    captured: dict = {}

    def fake_build(settings, *, securo_client_=None):
        captured.update(settings)
        return {"counts": {}, "transactions": [], "missing": [], "totals": {}, "advice": [], "source": {}}

    monkeypatch.setattr(core, "hermes_settings", lambda: {"securo_url": "http://config.home", "securo_token": "sct_x"})
    monkeypatch.setattr(core, "build_ledger", fake_build)

    core.scan()

    assert captured["securo_url"] == "http://config.home"
    assert captured["securo_token"] == "sct_x"
