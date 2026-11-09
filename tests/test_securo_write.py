"""Attachment writes exercised over real HTTP, with a local Securo stub."""
from __future__ import annotations

import json
import sys
import threading
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
import securo_client

TOKEN = "private-stub-bearer-token"


@pytest.fixture
def server():
    state = {"requests": [], "status": 201, "attachments": [], "exists": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(("GET", self.path, dict(self.headers), b""))
            if state.get("read_status"):
                return self.send_json(state["read_status"], {"detail": TOKEN})
            if not state["exists"]:
                return self.send_json(404, {"detail": TOKEN})
            if self.path == "/api/accounts":
                return self.send_json(200, [])
            if self.path.startswith("/api/transactions?"):
                return self.send_json(200, {"items": [{"id": "t1", "date": "2026-08-27", "description": "Vendor", "amount": 12.34, "type": "debit", "attachment_count": 0}], "total": 1})
            if self.path == "/api/transactions/t1":
                return self.send_json(200, {"id": "t1"})
            if self.path == "/api/transactions/t1/attachments":
                return self.send_json(200, state["attachments"])
            return self.send_json(404, {"detail": TOKEN})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state["requests"].append(("POST", self.path, dict(self.headers), body))
            if self.path == "/api/auth/login":
                return self.send_json(200, {"access_token": TOKEN})
            if state["status"] >= 400:
                return self.send_json(state["status"], {"detail": TOKEN})
            message = BytesParser(policy=default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body
            )
            part, = message.iter_parts()
            record = {"id": "a1", "filename": part.get_filename(), "size": len(part.get_payload(decode=True))}
            state["attachments"].append(record)
            self.send_json(201, state.get("upload_response", {**record, "access_token": TOKEN}))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_port}"
    yield state
    httpd.shutdown()
    httpd.server_close()
    thread.join()


@pytest.fixture
def receipt(tmp_path):
    path = tmp_path / "Vendor receipt.pdf"
    path.write_bytes(b"%PDF-1.4\r\nreal binary receipt\x00\xff")
    return path


def writer(server, **kwargs):
    import securo_write
    return securo_write.SecuroWriter(securo_client.SecuroClient(server["url"], token=TOKEN), **kwargs)


def test_upload_sends_real_multipart_and_returns_only_metadata(server, receipt):
    result = writer(server).upload_attachment("t1", receipt)
    assert result == {"id": "a1", "filename": receipt.name, "size": receipt.stat().st_size}
    method, path, headers, body = server["requests"][0]
    assert (method, path) == ("POST", "/api/transactions/t1/attachments")
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + headers["Content-Type"].encode() + b"\r\n\r\n" + body
    )
    part, = message.iter_parts()
    assert part.get_param("name", header="content-disposition") == "file"
    assert part.get_filename() == receipt.name
    assert part.get_content_type() == "application/pdf"
    assert part.get_payload(decode=True) == receipt.read_bytes()
    assert TOKEN not in json.dumps(result)
    # Read back the exact attachment target to prove the stub persisted it.
    assert securo_client.SecuroClient(server["url"], token=TOKEN).attachments("t1") == [result]


@pytest.mark.parametrize("status, text", [(401, "credential"), (403, "permission"), (404, "transaction"), (413, "too large"), (500, "500")])
def test_upload_maps_errors_without_echoing_server_secrets(server, receipt, status, text):
    server["status"] = status
    with pytest.raises(securo_client.SecuroError) as caught:
        writer(server).upload_attachment("t1", receipt)
    assert caught.value.status == status
    assert text in str(caught.value).lower()
    assert caught.value.hint
    assert TOKEN not in json.dumps(caught.value.as_dict())
    assert len(server["requests"]) == 1  # no automatic write retries


@pytest.mark.parametrize("kind", ["large", "empty", "directory", "missing", "unsafe_name"])
def test_local_file_rejected_before_any_request(server, receipt, tmp_path, kind):
    source = receipt
    kwargs = {}
    if kind == "large":
        with source.open("wb") as handle:
            handle.truncate(25 * 1024 * 1024 + 1)
    elif kind == "empty":
        source.write_bytes(b"")
    elif kind == "directory":
        source = tmp_path
    elif kind == "missing":
        source = tmp_path / "absent.pdf"
    else:
        kwargs["filename"] = "evil\r\nInjected: yes.pdf"
    with pytest.raises(securo_client.SecuroError) as caught:
        writer(server).upload_attachment("t1", source, **kwargs)
    assert caught.value.hint
    assert server["requests"] == []


def test_configurable_size_limit(server, receipt):
    with pytest.raises(securo_client.SecuroError, match="too large"):
        writer(server, max_bytes=1).upload_attachment("t1", receipt)
    assert server["requests"] == []


@pytest.fixture
def api(server, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import plugin_api
    row = {"id": "securo:t1", "securo_id": "t1", "date": "2026-08-27", "vendor": "Vendor", "amount": 12.34}
    ledger = {"source": {"kind": "securo", "securo_url": server["url"]}, "transactions": [row]}
    monkeypatch.setattr(plugin_api.core, "load_ledger", lambda: ledger)
    monkeypatch.setattr(plugin_api.core, "effective_settings", lambda: {"securo_url": server["url"], "securo_token": TOKEN})
    app = FastAPI()
    app.include_router(plugin_api.router)
    with TestClient(app) as client:
        yield client, ledger


def test_preview_checks_live_target_and_count_without_posting(api, server, receipt):
    client, _ = api
    server["attachments"] = [{"id": "old"}]
    response = client.post("/securo/attach/preview", json={"transaction_id": "t1", "path": str(receipt)})
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["target"] == {"date": "2026-08-27", "vendor": "Vendor", "amount": 12.34}
    assert data["file"] == {"name": receipt.name, "bytes": receipt.stat().st_size}
    assert data["attachment_count"] == 1
    assert data["warnings"]
    assert {entry[1] for entry in server["requests"]} == {"/api/transactions/t1", "/api/transactions/t1/attachments"}
    assert not any(entry[0] == "POST" for entry in server["requests"])
    assert TOKEN not in response.text


@pytest.mark.parametrize("confirm", [None, False, "true", 1, [], {}])
def test_attach_requires_literal_true_before_any_request(api, server, receipt, confirm):
    client, _ = api
    response = client.post("/securo/attach", json={"transaction_id": "t1", "path": str(receipt), "confirm": confirm})
    assert response.json()["ok"] is False
    assert "confirm" in response.json()["error"].lower()
    assert server["requests"] == []
    assert TOKEN not in response.text


def test_attach_reports_filename_size_and_persists_correct_target(api, server, receipt):
    client, _ = api
    response = client.post("/securo/attach", json={"transaction_id": "securo:t1", "path": str(receipt), "confirm": True})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "attached": {"filename": receipt.name, "size": receipt.stat().st_size}, "transaction_id": "t1"}
    assert TOKEN not in response.text
    assert securo_client.SecuroClient(server["url"], token=TOKEN).attachments("t1") == [{"id": "a1", "filename": receipt.name, "size": receipt.stat().st_size}]
    assert len([entry for entry in server["requests"] if entry[0] == "POST"]) == 1


@pytest.mark.parametrize("route", ["/securo/attach/preview", "/securo/attach"])
@pytest.mark.parametrize("kind", ["missing_scan", "unknown", "gone", "extension", "missing_file", "empty", "directory", "large", "wrong_instance"])
def test_routes_refuse_invalid_targets_and_files(api, server, receipt, route, kind):
    client, ledger = api
    transaction_id = "t1"
    if kind == "missing_scan":
        ledger.clear()
    elif kind == "unknown":
        transaction_id = "not-in-scan"
    elif kind == "gone":
        server["exists"] = False
    elif kind == "extension":
        receipt = receipt.with_suffix(".exe")
        receipt.write_bytes(b"unsafe")
    elif kind == "missing_file":
        receipt.unlink()
    elif kind == "empty":
        receipt.write_bytes(b"")
    elif kind == "directory":
        receipt = receipt.parent
    elif kind == "large":
        with receipt.open("wb") as handle:
            handle.truncate(25 * 1024 * 1024 + 1)
    else:
        ledger["source"]["securo_url"] = "https://other-instance.invalid"
    response = client.post(route, json={"transaction_id": transaction_id, "path": str(receipt), "confirm": True})
    assert response.json()["ok"] is False
    assert response.json()["hint"]
    assert TOKEN not in response.text
    assert not any(entry[0] == "POST" for entry in server["requests"])


@pytest.mark.parametrize("status", [401, 403, 404, 413, 500])
def test_attach_route_maps_upload_failure_without_secret(api, server, receipt, status):
    client, _ = api
    server["status"] = status
    response = client.post("/securo/attach", json={"transaction_id": "t1", "path": str(receipt), "confirm": True})
    assert response.json()["ok"] is False
    assert response.json()["status"] == status
    assert response.json()["hint"]
    assert TOKEN not in response.text


@pytest.mark.parametrize("route", ["/securo/attach/preview", "/securo/attach"])
@pytest.mark.parametrize("body", [[], None, {"transaction_id": 1, "path": []}, {}])
def test_routes_map_invalid_json_input(api, server, route, body):
    client, _ = api
    response = client.post(route, content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert response.json()["ok"] is False
    assert TOKEN not in response.text
    assert server["requests"] == []


@pytest.mark.parametrize("route", ["/securo/attach/preview", "/securo/attach"])
def test_unreadable_file_is_mapped_before_network(api, server, receipt, route):
    client, _ = api
    receipt.chmod(0)
    try:
        response = client.post(route, json={"transaction_id": "t1", "path": str(receipt), "confirm": True})
        assert response.json()["ok"] is False
        assert "readable" in response.json()["error"]
        assert server["requests"] == []
        assert TOKEN not in response.text
    finally:
        receipt.chmod(0o600)


def test_preview_in_password_mode_only_posts_login(api, server, receipt, monkeypatch):
    import plugin_api
    monkeypatch.setattr(plugin_api.core, "effective_settings", lambda: {"securo_url": server["url"], "securo_email": "owner@example.com", "securo_password": "stub-password"})
    client, _ = api
    response = client.post("/securo/attach/preview", json={"transaction_id": "t1", "path": str(receipt)})
    assert response.json()["ok"] is True
    assert [entry[1] for entry in server["requests"] if entry[0] == "POST"] == ["/api/auth/login"]
    assert TOKEN not in response.text
    assert "stub-password" not in response.text


def test_scan_remains_read_only(api, server, tmp_path, monkeypatch):
    import plugin_api
    monkeypatch.setattr(plugin_api.core, "DATA_DIR", tmp_path)
    monkeypatch.setattr(plugin_api.core, "LEDGER_PATH", tmp_path / "ledger.json")
    client, _ = api
    response = client.post("/scan")
    assert response.status_code == 200
    assert response.json()["transactions"][0]["securo_id"] == "t1"
    assert not any(entry[0] == "POST" for entry in server["requests"])
    assert TOKEN not in response.text


@pytest.mark.parametrize("status", [401, 403, 404, 500])
def test_preview_maps_read_failures_without_secret(api, server, receipt, status):
    client, _ = api
    server["read_status"] = status
    response = client.post("/securo/attach/preview", json={"transaction_id": "t1", "path": str(receipt)})
    assert response.json()["ok"] is False
    assert response.json()["status"] == status
    assert TOKEN not in response.text
    assert not any(entry[0] == "POST" for entry in server["requests"])


def test_filename_override_is_the_multipart_filename(server, receipt):
    result = writer(server).upload_attachment("t1", receipt, filename='Renamed "receipt".pdf')
    assert result["filename"] == 'Renamed "receipt".pdf'
    assert server["attachments"] == [result]


@pytest.mark.parametrize("payload", [{}, [], {"id": None}, {"id": TOKEN}])
def test_invalid_upload_metadata_is_safe_and_not_retried(server, receipt, payload):
    server["upload_response"] = payload
    with pytest.raises(securo_client.SecuroError) as caught:
        writer(server).upload_attachment("t1", receipt)
    assert "before retrying" in caught.value.hint
    assert TOKEN not in json.dumps(caught.value.as_dict())
    assert len(server["requests"]) == 1


@pytest.mark.parametrize("route", ["/securo/attach/preview", "/securo/attach"])
def test_malformed_attachment_list_is_a_safe_error(api, server, receipt, route):
    server["attachments"] = TOKEN
    client, _ = api
    response = client.post(route, json={"transaction_id": "t1", "path": str(receipt), "confirm": True})
    assert response.json()["ok"] is False
    assert TOKEN not in response.text
    assert not any(entry[0] == "POST" for entry in server["requests"])
