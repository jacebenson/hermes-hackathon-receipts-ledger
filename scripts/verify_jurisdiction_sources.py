#!/usr/bin/env python3
"""Check URL reachability without treating a successful request as rule verification."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag
from urllib.request import HTTPRedirectHandler, Request, build_opener

from validate_jurisdictions import DEFAULT_DIRECTORY, valid_https

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 ReceiptLedgerSourceCheck/1.0"


class HTTPSRedirects(HTTPRedirectHandler):
    """Do not silently follow an official HTTPS link onto plaintext transport."""
    max_redirections = 5
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_https(newurl):
            raise HTTPError(req.full_url, code, "redirect is not HTTPS", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_url(url: str, timeout: float = 8.0) -> dict:
    """Use a bounded GET, reading one byte, so HEAD-only blocks do not look dead."""
    result = {"url": url, "code": None, "status": "unconfirmed", "error": ""}
    if not valid_https(url):
        result["error"] = "not an absolute HTTPS URL"
        return result
    try:
        request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,*/*;q=0.8"}, method="GET")
        with build_opener(HTTPSRedirects()).open(request, timeout=timeout) as response:
            response.read(1)
            result["code"] = response.status
            result["status"] = "ok" if 200 <= response.status < 300 else "blocked"
    except HTTPError as exc:
        result["code"] = exc.code
        result["error"] = str(exc.reason)
        # The host answered, so the official domain is right; it just refused an
        # automated request. A browser will get further than we did.
        result["status"] = "blocked"
    except (URLError, OSError, TimeoutError, ValueError, socket.timeout) as exc:
        result["error"] = str(exc)
    return result


def collect_urls(records: list[dict]) -> list[str]:
    """Check each distinct request once, including provenance and obligation links."""
    urls = set()
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("url", "source_url") and isinstance(item, str):
                    urls.add(urldefrag(item)[0])
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(records)
    return sorted(urls)


def update_sources(record: dict, results: dict, checked_on: str) -> None:
    """Only change reachability metadata; never promote confidence or rewrite quotes."""
    for source in record["sources"].values():
        source["status"] = results[urldefrag(source["url"])[0]]["status"]
        source["checked_on"] = checked_on


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--write", action="store_true", help="update source status and checked_on only")
    parser.add_argument("--timeout", type=float, default=8.0, help="per-request timeout in seconds, maximum 30")
    args = parser.parse_args()
    if not 0 < args.timeout <= 30:
        parser.error("--timeout must be greater than 0 and at most 30")
    paths = sorted(args.directory.glob("*.json"))
    if not paths:
        parser.error("no jurisdiction JSON files found")
    try:
        records = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    urls = collect_urls(records)
    with ThreadPoolExecutor(max_workers=12) as pool:
        rows = list(pool.map(lambda url: check_url(url, args.timeout), urls))
    print("URL\tHTTP\tSTATUS\tDETAIL")
    for row in rows:
        detail = row["error"].replace("\n", " ").replace("\t", " ")
        print(f"{row['url']}\t{row['code'] if row['code'] is not None else '-'}\t{row['status']}\t{detail}")
    answered = sum(row["code"] is not None for row in rows)
    ok = sum(row["status"] == "ok" for row in rows)
    blocked = sum(row["status"] == "blocked" for row in rows)
    print(f"Summary: {len(rows)} URLs; {answered} HTTP responses; {ok} ok; {blocked} blocked; {len(rows) - ok - blocked} unconfirmed")
    if args.write:
        results = {row["url"]: row for row in rows}
        for path, record in zip(paths, records):
            update_sources(record, results, date.today().isoformat())
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Updated source status/checked_on only. Run the validator and review any confidence conflicts.")
    return int(ok != len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
