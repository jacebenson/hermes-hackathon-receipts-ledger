#!/usr/bin/env python3
"""Pull candidate filing sentences out of a state's official pages.

Nothing here writes a record. It fetches the sources a record already cites and
looks for sentences that name a date and a filing act, so a human (or a
reviewing agent) starts from real quotes instead of a blank form.

The point of separating this from the records is honesty: a machine finding a
sentence is not the same as a person reading the rule. Candidates land in
jurisdictions/_candidates/<id>.json with the exact sentence, the URL it came
from, and the fetch date. Promotion is a separate, deliberate step.

Usage:
  python3 scripts/extract_obligations.py --id us-ca          # one state, printed
  python3 scripts/extract_obligations.py --all --write       # candidates to disk
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
MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
DATE_SIGNAL = re.compile(rf"({MONTHS})\s+\d{{1,2}}|\b\d{{1,2}}/\d{{1,2}}|by the \d", re.IGNORECASE)
FILING_SIGNAL = re.compile(
    r"annual report|annual renewal|renewal|must file|shall file|due|franchise tax|"
    r"statement of information|registered agent|biennial|return",
    re.IGNORECASE,
)
NOISE = re.compile(r"cookie|privacy policy|subscribe|newsletter|javascript|browser", re.IGNORECASE)


def fetch(url: str, timeout: float = 20.0) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace")


def text_of(html: str) -> str:
    """Rough readable text: drop scripts and styles, keep sentence punctuation."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    text = unescape(html).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if 40 <= len(part.strip()) <= 400]


def candidates_for(record: dict, pages: int = 3) -> dict:
    """Up to `pages` usable sentences per record, with their source."""
    found: list[dict] = []
    used_urls: set[str] = set()
    for key in ("registry", "revenue", "sales_tax", "withholding"):
        source = (record.get("sources") or {}).get(key) or {}
        url = source.get("url") or ""
        if not url or source.get("status") != "ok" or url in used_urls:
            continue
        if len({item["source_url"] for item in found}) >= pages:
            break
        used_urls.add(url)
        try:
            body = text_of(fetch(url))
        except Exception as error:  # noqa: BLE001 — a failed fetch is a finding, not a crash
            continue
        scored = []
        for sentence in sentences(body):
            if NOISE.search(sentence):
                continue
            if DATE_SIGNAL.search(sentence) and FILING_SIGNAL.search(sentence):
                score = 2 + (1 if "annual" in sentence.lower() else 0)
                scored.append((score, sentence))
        scored.sort(key=lambda item: -item[0])
        seen = set()
        for score, sentence in scored[:2]:
            normalised = re.sub(r"\W+", " ", sentence.lower()).strip()
            if normalised in seen:
                continue
            seen.add(normalised)
            found.append(
                {
                    "source_key": key,
                    "source_url": url,
                    "quote": sentence,
                    "score": score,
                    "fetched_on": date.today().isoformat(),
                }
            )
    return {"id": record["id"], "name": record.get("name", ""), "candidates": found}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", help="one jurisdiction id, e.g. us-ca")
    parser.add_argument("--all", action="store_true", help="every seeded or unknown record")
    parser.add_argument("--write", action="store_true", help="save under jurisdictions/_candidates/")
    parser.add_argument("--pages", type=int, default=3)
    args = parser.parse_args()

    records = []
    for path in sorted(BUNDLE.glob("us-*.json")):
        record = json.loads(path.read_text())
        if args.id and record["id"] != args.id:
            continue
        if not args.id and not (args.all and record.get("confidence") != "verified"):
            continue
        records.append(record)

    CANDIDATES.mkdir(parents=True, exist_ok=True)
    total = 0
    for record in records:
        result = candidates_for(record, pages=args.pages)
        total += len(result["candidates"])
        if args.write:
            (CANDIDATES / f"{record['id']}.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"{record['id']:10} {record.get('name','')[:16]:18} {len(result['candidates'])} candidate(s)")
        for item in result["candidates"]:
            print(f"   [{item['source_key']}] {item['quote'][:150]}")
    print(f"\n{len(records)} record(s), {total} candidate(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
