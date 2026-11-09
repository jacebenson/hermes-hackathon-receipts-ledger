"""The jurisdiction bundle, read as data.

Nothing here decides tax treatment. Each record is a claim with a source and a
date, and the only computation performed is the obvious one: when a due rule
starts with a plain month and day ("December 31"), work out the next time that
date comes around. Anything phrased as a rule ("the 15th day of the 3rd month
after year end") is passed through untouched, because guessing it would be
exactly the invented deadline this bundle exists to avoid.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

BUNDLE_DIR = Path(__file__).resolve().parent.parent / "jurisdictions"
CONFIDENCES = ("verified", "extracted", "seeded", "unknown")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_DAY = re.compile(r"^(%s)\s+(\d{1,2})\b" % "|".join(MONTHS), re.IGNORECASE)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("id"):
        return None
    return record


def records(directory: Path | None = None) -> list[dict[str, Any]]:
    root = Path(directory or BUNDLE_DIR)
    if not root.is_dir():
        return []
    loaded = [_load(path) for path in sorted(root.glob("us-*.json"))]
    return [record for record in loaded if record is not None]


def catalog(directory: Path | None = None) -> dict[str, Any]:
    """Everything the pane needs to list jurisdictions without loading details."""
    bundle = records(directory)
    counts: dict[str, int] = {name: 0 for name in CONFIDENCES}
    entries = []
    for record in bundle:
        confidence = str(record.get("confidence", "unknown"))
        if confidence not in counts:
            confidence = "unknown"
        counts[confidence] += 1
        entries.append(
            {
                "id": record["id"],
                "name": record.get("name", record["id"]),
                "level": record.get("level", "state"),
                "confidence": confidence,
                "obligations": len(record.get("obligations") or []),
                "verified_on": record.get("updated_on", ""),
            }
        )
    entries.sort(key=lambda item: (item["level"] != "federal", item["name"]))
    return {"counts": counts, "records": entries}


def next_month_day(due_rule: str, today: date | None = None) -> str:
    """The next occurrence of a plain 'Month day' due rule, or '' when unsure."""
    match = _MONTH_DAY.match((due_rule or "").strip())
    if not match:
        return ""
    month, day = MONTHS[match.group(1).lower()], int(match.group(2))
    today = today or date.today()
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return ""
        if candidate >= today:
            return candidate.isoformat()
    return ""


def record(jurisdiction_id: str, directory: Path | None = None) -> dict[str, Any] | None:
    """One record, with the parsed next due date attached to each obligation."""
    for candidate in records(directory):
        if candidate["id"] != jurisdiction_id:
            continue
        enriched = dict(candidate)
        obligations = []
        for obligation in candidate.get("obligations") or []:
            item = dict(obligation)
            item["next_due"] = next_month_day(str(item.get("due_rule", "")))
            obligations.append(item)
        enriched["obligations"] = obligations
        return enriched
    return None


def summary(directory: Path | None = None) -> dict[str, Any]:
    """What a reader should know before trusting any of it."""
    data = catalog(directory)
    unknown = [entry["name"] for entry in data["records"] if entry["confidence"] == "unknown"]
    return {
        **data,
        "unknown_names": unknown,
        "statement": (
            f"{data['counts']['verified']} jurisdictions carry rules a person read and quoted, "
            f"{data['counts']['extracted']} carry quotes copied from the live page but not yet reviewed, "
            f"{data['counts']['seeded']} have sources only, and "
            f"{data['counts']['unknown']} could not be reached at all."
        ),
    }
