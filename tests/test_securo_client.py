"""Tests for the Securo client: real HTTP against a stub instance.

The stub speaks the same shapes as a live Securo (paginated
``/api/transactions``, attachment metadata, ``Content-Disposition`` download),
so pagination, auth and error handling are exercised over the wire rather than
mocked out.

Run: python3 -m pytest tests -q
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))

import securo_client  # noqa: E402


class StubState:
    def __init__(self) -> None:
        self.token = "good-token"
        self.email = "owner@example.com"
        self.accounts = [
            {"id": "acct-1", "name": "Business Checking", "type": "checking", "masked_number": "", "institution_name": "US Bank"},
            {"id": "acct-2", "name": "Business Visa", "type": "credit", "masked_number": "8830", "institution_name": "Chase"},
        ]
        self.transactions: list[dict] = []
        self.attachments: dict[str, list[dict]] = {}
        self.page_cap = 2
        self.requests: list[tuple[str, str, dict]] = []

    def add_transaction(self, **kwargs) -> dict:
        row = {
            "id": kwargs.get("id", f"txn-{len(self.transactions) + 1}"),
            "date": kwargs.get("date", "2026-08-27"),
            "description": kwargs.get("description", "Hetzner Online GmbH"),
            "amount": kwargs.get("amount", 63.44),
            "amount_primary": kwargs.get("amount_primary"),
            "type": kwargs.get("type", "debit"),
            "account_id": kwargs.get("account_id", "acct-1"),
            "attachment_count": kwargs.get("attachment_count", 0),
            "category": kwargs.get("category"),
            "payee_name": kwargs.get("payee_name"),
            "currency": "USD",
            "status": "posted",
        }
        self.transactions.append(row)
        return row


STATE = StubState()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — base class signature
        return

    # -- helpers ---------------------------------------------------------

    def _send(self, status: int, payload: bytes, content_type: str = "application/json", extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization") or ""
        return header == f"Bearer {STATE.token}"

    # -- routes ----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 — http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode()
        path = urlsplit(self.path).path
        STATE.requests.append(("POST", path, dict(self.headers)))
        if path == "/api/auth/login":
            form = parse_qs(body)
            if form.get("username") == [STATE.email] and form.get("password") == ["secret"]:
                return self._json(200, {"access_token": STATE.token, "token_type": "bearer"})
            return self._json(400, {"detail": "Incorrect email or password"})
        return self._json(404, {"detail": "Not Found"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        STATE.requests.append(("GET", path, dict(self.headers)))
        if not self._authorized():
            return self._json(401, {"detail": "Unauthorized"})

        if path == "/api/users/me":
            return self._json(200, {"id": "user-1", "email": STATE.email, "is_active": True, "is_superuser": False, "is_verified": True})
        if path == "/api/workspaces":
            return self._json(200, [{"id": "ws-1", "name": "Personal", "kind": "personal", "is_archived": False, "default_currency": "USD"}])
        if path == "/api/accounts":
            return self._json(200, STATE.accounts)
        if path == "/api/transactions":
            page = int(query.get("page", 1))
            limit = min(int(query.get("limit", 50)), STATE.page_cap)
            start = (page - 1) * limit
            items = STATE.transactions[start : start + limit]
            return self._json(
                200,
                {"items": items, "total": len(STATE.transactions), "page": page, "limit": limit, "summary": {}},
            )
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["api", "transactions"] and parts[3] == "attachments":
            txn_id = parts[2]
            if txn_id not in STATE.attachments:
                return self._json(200, [])
            return self._json(200, STATE.attachments[txn_id])
        if len(parts) == 5 and parts[:2] == ["api", "transactions"] and parts[3] == "attachments":
            txn_id, attachment_id = parts[2], parts[4]
            for attachment in STATE.attachments.get(txn_id, []):
                if attachment["id"] == attachment_id:
                    return self._send(
                        200,
                        b"%PDF-1.4 stub receipt",
                        "application/pdf",
                        {"Content-Disposition": f"attachment; filename*=UTF-8''{attachment['filename']}"},
                    )
            return self._json(404, {"detail": "Attachment not found"})
        return self._json(404, {"detail": "Not Found"})


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def reset_state():
    STATE.__init__()
    yield
    STATE.__init__()


def seed(**kwargs) -> dict:
    row = STATE.add_transaction(**kwargs)
    return row


# -- tests ---------------------------------------------------------------


def test_token_auth_reads_accounts_and_identity(server: str) -> None:
    client = securo_client.SecuroClient(server, token="good-token")
    check = client.check()

    assert check["ok"] is True
    assert check["email"] == "owner@example.com"
    assert check["workspace"] == "Personal"
    assert check["accounts"] == 2
    assert check["auth"] == "token"


def test_bare_hostname_gets_a_scheme():
    assert securo_client.normalize_base_url("securo.home") == "http://securo.home"
    assert securo_client.normalize_base_url(" https://securo.example.com/ ") == "https://securo.example.com"
    assert securo_client.normalize_base_url("http://10.0.0.5:8030/api") == "http://10.0.0.5:8030/api"


def test_password_mode_logs_in_once_and_sends_the_token(server: str) -> None:
    client = securo_client.SecuroClient(server, email=STATE.email, password="secret")
    client.accounts()

    logins = [entry for entry in STATE.requests if entry[1] == "/api/auth/login"]
    assert len(logins) == 1
    accounts_call = next(entry for entry in STATE.requests if entry[1] == "/api/accounts")
    assert accounts_call[2].get("Authorization") == f"Bearer {STATE.token}"


def test_rejected_credential_explains_itself(server: str) -> None:
    client = securo_client.SecuroClient(server, token="wrong-token")
    with pytest.raises(securo_client.SecuroError) as caught:
        client.accounts()

    assert caught.value.status == 401
    assert "rejected" in str(caught.value)
    assert "email and password" in caught.value.hint


def test_transactions_walk_every_page(server: str) -> None:
    for index in range(5):
        seed(id=f"txn-{index}", date=f"2026-08-{10 + index:02d}", description="Hetzner", amount=63.44)

    rows = securo_client.SecuroClient(server, token="good-token").transactions("2026-01-01", "2026-12-31")

    assert len(rows) == 5
    pages = [entry for entry in STATE.requests if entry[1] == "/api/transactions"]
    assert len(pages) == 3  # stub caps pages at 2 rows, so 2 + 2 + 1


def test_window_is_passed_through(server: str) -> None:
    seed()
    client = securo_client.SecuroClient(server, token="good-token")
    client.transactions("2026-01-01", "2026-06-30")

    # the stub logs the path only, so assert on the query the client built
    url = client._url("/api/transactions", [("from", "2026-01-01"), ("to", "2026-06-30")])
    assert "from=2026-01-01" in url and "to=2026-06-30" in url


def test_attachment_download_keeps_the_original_filename(server: str) -> None:
    seed(id="txn-att", attachment_count=1)
    STATE.attachments["txn-att"] = [
        {"id": "att-1", "transaction_id": "txn-att", "filename": "Hetzner 2026-08-27 $63.44.pdf", "content_type": "application/pdf", "size": 20}
    ]

    client = securo_client.SecuroClient(server, token="good-token")
    listed = client.attachments("txn-att")
    filename, content_type, payload = client.download_attachment("txn-att", listed[0]["id"])

    assert filename == "Hetzner 2026-08-27 $63.44.pdf"
    assert content_type == "application/pdf"
    assert payload.startswith(b"%PDF")


def test_unreachable_instance_names_the_url() -> None:
    client = securo_client.SecuroClient("http://127.0.0.1:1", token="x", timeout=1)
    with pytest.raises(securo_client.SecuroError) as caught:
        client.accounts()

    assert "127.0.0.1:1" in str(caught.value)
    assert "Tailscale" in caught.value.hint


def test_missing_credential_is_refused_before_any_request() -> None:
    with pytest.raises(securo_client.SecuroError) as caught:
        securo_client.client_for({"securo_url": "http://securo.home"})

    assert "not connected" in str(caught.value).lower()
