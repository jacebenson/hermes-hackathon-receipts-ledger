#!/usr/bin/env python3
"""Promote reviewed candidates into records, but only if the quote still exists.

An extracted obligation is worth less than a human-read one and more than
nothing, provided the sentence is really on the page. So this script re-fetches
every cited URL and refuses any candidate whose quote is not a literal substring
of what came back. A model that hallucinated a deadline cannot get it into the
bundle through this door.

Promotion sets `confidence: extracted` — quoted from the live page, not reviewed
by a person. A human promotes it to `verified` by reading it and saying so.

Usage:
  python3 scripts/promote_candidates.py --dry-run
  python3 scripts/promote_candidates.py
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "jurisdictions"
CANDIDATES = BUNDLE / "_candidates"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36 receipt-ledger/1.0"
)


def _text(url: str, timeout: float = 20.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
    body = raw.decode(charset, "replace")
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", unescape(body)).strip()


def quote_is_present(quote: str, page: str) -> bool:
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    wanted = normalise(quote)
    return bool(wanted) and wanted in normalise(page)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    promoted, refused = 0, []
    for path in sorted(CANDIDATES.glob("us-*.json")):
        candidate = json.loads(path.read_text())
        if not candidate.get("found") or not candidate.get("obligations"):
            refused.append((candidate["id"], "nothing found"))
            continue
        record_path = BUNDLE / f"{candidate['id']}.json"
        if not record_path.exists():
            refused.append((candidate["id"], "no record to promote into"))
            continue
        record = json.loads(record_path.read_text())
        if record.get("confidence") == "verified":
            refused.append((candidate["id"], "already verified by a person"))
            continue

        accepted = []
        for obligation in candidate["obligations"]:
            url = obligation.get("source_url", "")
            try:
                page = _text(url)
            except Exception as error:  # noqa: BLE001 — an unreachable page is a refusal
                refused.append((f"{candidate['id']}:{obligation.get('id')}", f"source unreachable ({error})"))
                continue
            if not quote_is_present(obligation.get("quote", ""), page):
                # The whole point of this script: no quote, no promotion.
                refused.append((f"{candidate['id']}:{obligation.get('id')}", "quote not found on the page"))
                continue
            accepted.append({**obligation, "verified_on": date.today().isoformat()})

        if not accepted:
            continue
        existing = {item.get("id") for item in record.get("obligations") or []}
        merged = [item for item in record.get("obligations") or []]
        merged.extend(item for item in accepted if item.get("id") not in existing)
        record["obligations"] = merged
        record["confidence"] = "extracted"
        record["updated_on"] = date.today().isoformat()
        note = "Quote copied from the live page by a script and re-verified before promotion; not yet read by a person."
        if note not in (record.get("notes") or ""):
            record["notes"] = ((record.get("notes") or "").strip() + " " + note).strip()
        promoted += len(accepted)
        if not args.dry_run:
            record_path.write_text(json.dumps(record, indent=2) + "\n")
        print(f"{record['id']:10} promoted {len(accepted)} obligation(s)")

    print(f"\n{promoted} obligation(s) {'would be ' if args.dry_run else ''}promoted")
    if refused:
        print("refused:")
        for identifier, reason in refused:
            print(f"  {identifier:26} {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
