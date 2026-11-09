"""The journal of what the plugin read and wrote.

The point of these tests is the promise, not the plumbing: a scan is recorded, a
write is recorded with its outcome, failures are visible, the file cannot grow
without bound, and the journal never becomes a reason the user's operation fails.

Run: .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import audit  # noqa: E402
import ledger_core  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(ledger_core, "DATA_DIR", tmp_path)
    return tmp_path


def test_a_scan_is_recorded_with_its_counts(data_dir: Path) -> None:
    entry = audit.record("scan", detail="scanned", counts={"charges": 6, "missing": 2})

    assert entry is not None
    assert entry["action"] == "scan"
    assert entry["ok"] is True
    latest = audit.recent(1)[0]
    assert latest["counts"] == {"charges": 6, "missing": 2}
    assert latest["at"].count("-") == 2  # ISO timestamp, not a locale string


def test_writes_and_failures_are_both_visible(data_dir: Path) -> None:
    audit.record("attach", ok=True, detail="attached", target="2026-08-27 Hetzner")
    audit.record("attach", ok=False, detail="413 too large", target="2026-09-22 OpenAI")

    summary = audit.summary()

    assert summary["last_write"]["detail"] == "413 too large"
    assert summary["failures"] == 1
    assert summary["by_action"]["attach"] == 2


def test_newest_entry_comes_first(data_dir: Path) -> None:
    audit.record("scan", detail="first")
    audit.record("package", detail="second")

    assert [entry["detail"] for entry in audit.recent(2)] == ["second", "first"]


def test_the_journal_is_bounded(data_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit, "MAX_ENTRIES", 5)
    for index in range(9):
        audit.record("scan", detail=f"scan-{index}")

    entries = audit.recent(100)

    assert len(entries) == 5
    assert entries[0]["detail"] == "scan-8"
    assert entries[-1]["detail"] == "scan-4"
    assert len(audit.journal_path().read_text().strip().splitlines()) == 5


def test_a_corrupt_line_does_not_hide_the_rest(data_dir: Path) -> None:
    audit.record("scan", detail="good one")
    with audit.journal_path().open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    audit.record("package", detail="good two")

    entries = audit.recent(10)

    assert [entry["detail"] for entry in entries] == ["good two", "good one"]


def test_a_journal_that_cannot_be_written_never_breaks_the_operation(data_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(audit, "_dir", lambda: Path("/proc/definitely-not-writable"))

    assert audit.record("scan", detail="should not raise") is None
    assert audit.recent(5) == []


def test_an_unknown_action_is_journalled_as_an_error(data_dir: Path) -> None:
    entry = audit.record("totally-made-up", detail="typo")

    assert entry is not None
    assert entry["action"] == "error"


def test_limits_are_declared_as_data(data_dir: Path) -> None:
    titles = {item["title"] for item in audit.LIMITS}

    assert "Not tax advice" in titles
    assert "An attachment is paper, not proof" in titles
    assert all(item["detail"] for item in audit.LIMITS)
    assert audit.summary()["limits"] == audit.LIMITS


def test_the_journal_holds_no_secret(data_dir: Path) -> None:
    audit.record("settings", detail="saved", counts={"securo_token_set": True})
    raw = audit.journal_path().read_text()

    assert "sct_" not in raw
    assert "securo_token_set" in raw
    for line in raw.splitlines():
        json.loads(line)  # every line stays valid json
