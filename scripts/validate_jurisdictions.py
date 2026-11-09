#!/usr/bin/env python3
"""Validate the auditable data contract, not the truth of tax/legal rules."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "jurisdictions"
STATE_CODES = "al ak az ar ca co ct de dc fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy".split()
EXPECTED_IDS = {"us-federal", *(f"us-{code}" for code in STATE_CODES)}
CONFIDENCES = {"verified", "seeded", "unknown"}


def valid_date(value: object) -> bool:
    """Reject basic ISO variants so dates sort and compare consistently."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def valid_https(value: object) -> bool:
    """Require an absolute HTTPS URL without embedded credentials."""
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parts = urlsplit(value)
        return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
    except ValueError:
        return False


def validate_record(record: object, filename: str) -> list[str]:
    """Return all contract failures, including malformed nested values."""
    errors = []
    def error(message):
        errors.append(f"{filename}: {message}")
    def required_strings(obj, keys, label):
        for key in keys:
            if not isinstance(obj.get(key), str) or not obj[key].strip():
                error(f"{label}.{key} must be a nonempty string")
    if not isinstance(record, dict):
        return [f"{filename}: record must be an object"]
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        error("schema_version must be integer 1")
    required_strings(record, ("id", "name", "level", "confidence", "updated_on"), "record")
    if record.get("id") != Path(filename).stem:
        error("id must match filename stem")
    if not isinstance(record.get("level"), str) or record["level"] not in ("federal", "state"):
        error("level must be federal or state")
    elif record["level"] != ("federal" if Path(filename).stem == "us-federal" else "state"):
        error("level disagrees with jurisdiction id")
    confidence = record.get("confidence")
    if not isinstance(confidence, str) or confidence not in CONFIDENCES:
        error("confidence must be verified, seeded, or unknown")
    if not valid_date(record.get("updated_on")):
        error("updated_on must be an ISO date")
    if not isinstance(record.get("notes"), str):
        error("notes must be a string")
    sources = record.get("sources")
    if not isinstance(sources, dict):
        error("sources must be an object")
        sources = {}
    for key in ("revenue", "registry", "withholding"):
        if key not in sources:
            error(f"sources.{key} is required")
    for key, source in sources.items():
        label = f"sources.{key}"
        if not isinstance(source, dict):
            error(f"{label} must be an object")
            continue
        required_strings(source, ("url", "title", "checked_on", "status"), label)
        if not valid_https(source.get("url")):
            error(f"{label}.url must be an absolute https URL")
        if not valid_date(source.get("checked_on")):
            error(f"{label}.checked_on must be an ISO date")
        if source.get("status") not in ("ok", "blocked", "unconfirmed", "unchecked"):
            error(f"{label}.status is invalid")
        if confidence == "verified" and source.get("status") != "ok":
            error(f"verified record requires {label}.status ok")
        if confidence == "seeded" and source.get("status") in ("unconfirmed", "unchecked"):
            error(f"seeded record requires every source to have answered (ok or blocked)")
    obligations = record.get("obligations")
    if not isinstance(obligations, list):
        error("obligations must be an array")
        obligations = []
    if confidence == "verified" and not obligations:
        error("verified record requires at least one obligation")
    if confidence == "unknown" and obligations:
        error("unknown record cannot have obligations")
    if confidence == "seeded" and obligations:
        error("seeded record cannot have obligations")
    seen = set()
    for index, obligation in enumerate(obligations):
        label = f"obligations[{index}]"
        if not isinstance(obligation, dict):
            error(f"{label} must be an object")
            continue
        required_strings(obligation, ("id", "title", "applies_when", "form", "due_rule", "frequency", "source_url", "quote", "verified_on"), label)
        identifier = obligation.get("id")
        if isinstance(identifier, str):
            if identifier in seen:
                error(f"duplicate obligation id {identifier}")
            seen.add(identifier)
        if not valid_https(obligation.get("source_url")):
            error(f"{label}.source_url must be an absolute https URL")
        if not valid_date(obligation.get("verified_on")):
            error(f"{label}.verified_on must be an ISO date")
    return errors


def validate_bundle(directory: Path = DEFAULT_DIRECTORY) -> dict:
    """Check the exact roster so an extra file cannot conceal a missing state."""
    paths = sorted(directory.glob("*.json"))
    actual = {path.stem for path in paths}
    errors = []
    for identifier in sorted(EXPECTED_IDS - actual):
        errors.append(f"missing {identifier}.json")
    for identifier in sorted(actual - EXPECTED_IDS):
        errors.append(f"unexpected {identifier}.json")
    counts = Counter({key: 0 for key in sorted(CONFIDENCES)})
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        errors.extend(validate_record(record, path.name))
        if isinstance(record, dict) and isinstance(record.get("confidence"), str):
            counts[record["confidence"]] += 1
    return {"files": len(paths), "confidence": dict(counts), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--json", action="store_true", help="print machine-readable summary")
    args = parser.parse_args()
    result = validate_bundle(args.directory)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{result['files']} files; {len(result['errors'])} errors")
        for error in result["errors"]:
            print(error)
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
