"""The line between a machine quote and a fact.

Promotion is the only door into the bundle for a script-extracted obligation, so
these tests cover the door: a quote that is really on the page passes, a quote
that drifted fails, and whitespace differences do not matter.

Run: .venv/bin/python -m pytest tests -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))

spec = importlib.util.spec_from_file_location("promote_candidates", ROOT / "scripts" / "promote_candidates.py")
promote = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(promote)


PAGE = """
Annual Reports
Every limited liability company must file an annual report by April 15 of each year.
Filing after that date adds a late fee.
"""


def test_a_quote_that_is_really_on_the_page_passes() -> None:
    assert promote.quote_is_present("must file an annual report by April 15 of each year", PAGE)


def test_whitespace_and_line_breaks_do_not_break_a_real_quote() -> None:
    assert promote.quote_is_present("Every limited liability company must\nfile an annual report", PAGE)


def test_an_invented_deadline_is_refused() -> None:
    assert not promote.quote_is_present("must file an annual report by March 1 of each year", PAGE)


def test_an_empty_quote_is_never_promoted() -> None:
    assert not promote.quote_is_present("", PAGE)
    assert not promote.quote_is_present("   ", PAGE)


def test_the_bundle_reader_counts_every_tier(tmp_path: Path, monkeypatch) -> None:
    import jurisdiction_data

    record = {
        "schema_version": 1, "id": "us-tx", "name": "Texas", "level": "state",
        "confidence": "extracted", "updated_on": "2026-11-09",
        "sources": {"revenue": {"url": "https://example.gov/", "title": "t", "checked_on": "2026-11-09", "status": "ok"},
                    "registry": {"url": "https://example.gov/b", "title": "t", "checked_on": "2026-11-09", "status": "blocked"},
                    "withholding": {"url": "https://example.gov/c", "title": "t", "checked_on": "2026-11-09", "status": "ok"}},
        "obligations": [{"id": "tx-report", "title": "Report", "applies_when": "all", "form": "Report",
                         "due_rule": "May 15", "frequency": "annual", "source_url": "https://example.gov/",
                         "quote": "must file by May 15", "verified_on": "2026-11-09"}],
        "notes": "",
    }
    (tmp_path / "us-tx.json").write_text(__import__("json").dumps(record))

    summary = jurisdiction_data.summary(tmp_path)

    assert summary["counts"]["extracted"] == 1
    assert "not yet reviewed" in summary["statement"]
    assert jurisdiction_data.next_month_day("May 15", __import__("datetime").date(2026, 1, 1)) == "2026-05-15"
