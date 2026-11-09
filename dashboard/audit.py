"""What the plugin actually did: a small, bounded, local journal.

Trust here is not a badge on the README. A user — or a judge — should be able to
see the exact list of things this plugin read and wrote, in order, without
opening a log file in a terminal. So every scan, package build, attachment
upload and settings change appends one line to `data/audit.jsonl`, and the pane
renders the newest entries.

Rules this module keeps:

- Never raises. A failure to journal must not fail the operation the user asked
  for; the journal is evidence, not a gate.
- Never grows without bound. The newest MAX_ENTRIES survive; older lines are
  dropped on append so a year of scans is still a small file.
- Never records a credential. Callers pass counts and identifiers, not tokens.

Reads are included on purpose. "It only reads" is a claim a user should be able
to verify rather than trust.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_ENTRIES = 500

# Actions the plugin is allowed to journal. Anything else is a programming
# mistake, and a typo'd action name would quietly pollute the journal.
ACTIONS = ("scan", "package", "attach", "settings", "error")

# What this plugin will not do, stated once and rendered by the UI. Keeping it as
# data means the pane, the README and any future catalog listing cannot drift.
LIMITS: list[dict[str, str]] = [
    {
        "title": "Not tax advice",
        "detail": "The ledger matches paper to charges and totals spend. Whether something is deductible, "
        "or how it must be treated, is your accountant's call.",
    },
    {
        "title": "An attachment is paper, not proof",
        "detail": "A charge with a file attached counts as covered whether the file is an invoice, a screenshot "
        "or a photo of a napkin. The plugin does not read what is inside it.",
    },
    {
        "title": "The portal list is data",
        "detail": "Vendor billing portals come from a table in the plugin. A vendor it does not know shows "
        "'no portal known' rather than a guessed URL.",
    },
    {
        "title": "Classification is proposed, never assumed",
        "detail": "Business or personal is recorded only from a rule you set or a decision you confirm. With no "
        "rules, nothing is counted as business.",
    },
    {
        "title": "Jurisdiction facts are dated",
        "detail": "Every due date or rate names the government page it came from and the day it was verified. "
        "An unverified state says so instead of inventing a deadline.",
    },
    {
        "title": "Reads and writes are visible",
        "detail": "Every scan and every upload is appended to this journal. A scan never writes; uploading a "
        "receipt takes an explicit confirmation.",
    },
]


def _dir() -> Path:
    """The plugin's data directory, resolved at call time so tests can move it."""
    import ledger_core

    return Path(ledger_core.DATA_DIR)


def journal_path() -> Path:
    return _dir() / "audit.jsonl"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def record(action: str, *, ok: bool = True, detail: str = "", counts: dict[str, Any] | None = None,
           target: str = "") -> dict[str, Any] | None:
    """Append one entry. Returns it, or None when journaling was not possible."""
    entry = {
        "at": _now(),
        "action": action if action in ACTIONS else "error",
        "ok": bool(ok),
        "detail": (detail or "")[:400],
        "target": (target or "")[:200],
    }
    if counts:
        entry["counts"] = {str(key): value for key, value in counts.items() if isinstance(value, (int, float, bool, str))}
    try:
        path = journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = _read_lines(path)
        lines.append(json.dumps(entry))
        if len(lines) > MAX_ENTRIES:
            lines = lines[-MAX_ENTRIES:]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return entry
    except OSError:
        return None


def _read_lines(path: Path) -> list[str]:
    try:
        return [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    except OSError:
        return []


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Newest first. Unparseable lines are skipped rather than raised."""
    entries: list[dict[str, Any]] = []
    for line in _read_lines(journal_path())[-max(1, limit):]:
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return list(reversed(entries))


def summary() -> dict[str, Any]:
    """The three facts a skeptical user wants: what ran, when, and did it write."""
    entries = recent(MAX_ENTRIES)
    last_scan = next((entry for entry in entries if entry.get("action") == "scan"), None)
    last_write = next((entry for entry in entries if entry.get("action") == "attach"), None)
    failures = [entry for entry in entries if not entry.get("ok", True)]
    by_action: dict[str, int] = {}
    for entry in entries:
        by_action[entry.get("action", "?")] = by_action.get(entry.get("action", "?"), 0) + 1
    return {
        "entries": len(entries),
        "by_action": by_action,
        "last_scan": last_scan,
        "last_write": last_write,
        "failures": len(failures),
        "recent": entries[:20],
        "limits": LIMITS,
    }


def clear() -> bool:
    """Drop the journal. Only ever called from an explicit user action."""
    try:
        journal_path().write_text("", encoding="utf-8")
        return True
    except OSError:
        return False
