"""Securo client: read-only HTTP access to a self-hosted Securo instance.

Securo (https://github.com/securo-finance/securo) is a self-hosted personal
finance app. This module talks to its REST API and nothing else:

  POST /api/auth/login                       -> bearer token (email + password)
  GET  /api/users/me                         -> who the token belongs to
  GET  /api/workspaces                       -> workspaces the user can see
  GET  /api/accounts                         -> account names for the ledger
  GET  /api/transactions?from&to&page&limit  -> paginated charges
  GET  /api/transactions/{id}/attachments    -> receipt metadata
  GET  /api/transactions/{id}/attachments/{attachment_id} -> receipt bytes

No third-party dependencies: urllib from the standard library, so the plugin
runs inside any Hermes install without adding packages.

Two auth modes, because a self-hosted Securo user may have either:

  token     a long-lived access token (``sct_...``) minted in Securo. Preferred:
            scoped, revocable, never expires mid-scan.
  password  the email + password you use to sign in. The client logs in for a
            bearer token and re-logs in once when that token expires.

Nothing in this module logs, prints or returns a credential.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable

DEFAULT_TIMEOUT = 20.0
MAX_PAGES = 40
PAGE_SIZE = 500


class SecuroError(RuntimeError):
    """A Securo call that did not produce usable data.

    Carries enough context for the UI to say something true: which URL was
    tried, what came back, and what the user can do about it.
    """

    def __init__(self, message: str, *, status: int | None = None, hint: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.hint = hint

    def as_dict(self) -> dict[str, Any]:
        return {"error": str(self), "status": self.status, "hint": self.hint}


def normalize_base_url(raw: str) -> str:
    """Turn what a person types into a base URL.

    ``securo.home``, ``http://securo.home/``, ``https://securo.example.com``
    and a URL with a path prefix all work. Empty input raises.
    """
    text = (raw or "").strip()
    if not text:
        raise SecuroError("No Securo URL set.", hint="Enter the address you open Securo at, e.g. http://securo.home")
    if "://" not in text:
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise SecuroError(
            f"{parts.scheme!r} is not a web address.",
            hint="Use http:// or https:// — for a homelab Securo that is usually http://securo.home",
        )
    if not parts.netloc:
        raise SecuroError(f"Could not read a host out of {raw!r}.")
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request. Returns (status, headers, body) and never raises for 4xx/5xx."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read() or b""
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", error)
        raise SecuroError(
            f"Could not reach {url}: {reason}",
            hint="Check the URL, and that this machine can reach it (Tailscale, VPN, DNS).",
        ) from error
    except socket.timeout as error:
        raise SecuroError(
            f"{url} timed out after {timeout:g}s.",
            hint="The instance may be asleep or unreachable from here.",
        ) from error


class SecuroClient:
    """Read-only view of one Securo workspace."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        workspace_id: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self._token = (token or "").strip() or None
        self._email = (email or "").strip() or None
        self._password = password or None
        self.workspace_id = (workspace_id or "").strip() or None
        self.timeout = timeout
        self._transport = transport or _transport
        self._logged_in = False

    # -- plumbing ---------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "receipt-ledger/1.0 (+hermes plugin)"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self.workspace_id:
            headers["X-Workspace-Id"] = self.workspace_id
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str, params: Iterable[tuple[str, Any]] | None = None) -> str:
        url = f"{self.base_url}{path}"
        pairs = [(key, value) for key, value in (params or []) if value is not None]
        if pairs:
            url = f"{url}?{urllib.parse.urlencode(pairs, doseq=True)}"
        return url

    def _detail(self, payload: bytes) -> str:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            text = payload.decode("utf-8", "replace").strip()
            return text[:300]
        if isinstance(data, dict):
            detail = data.get("detail") or data.get("message") or data
            if isinstance(detail, list):
                parts = []
                for item in detail:
                    if isinstance(item, dict):
                        parts.append(str(item.get("msg") or item.get("detail") or item))
                    else:
                        parts.append(str(item))
                return "; ".join(parts)[:300]
            return str(detail)[:300]
        return str(data)[:300]

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: Iterable[tuple[str, Any]] | None = None,
        json_body: Any = None,
        form: dict[str, Any] | None = None,
        raw_response: bool = False,
        retry_auth: bool = True,
    ) -> Any:
        headers = self._headers()
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        url = self._url(path, params)
        status, response_headers, payload = self._transport(method, url, headers, body, self.timeout)

        if status == 401 and retry_auth and self._password and not self._token:
            self.login()
            headers = self._headers()
            status, response_headers, payload = self._transport(method, url, headers, body, self.timeout)

        if status == 401:
            raise SecuroError(
                "Securo rejected the credential.",
                status=status,
                hint=(
                    "Sign in with the same email and password you use in the Securo web app, "
                    "or paste an access token if your instance mints them."
                ),
            )
        if status == 403:
            raise SecuroError(
                "Securo refused this request for the signed-in user.",
                status=status,
                hint="The account may not be a member of the workspace you selected.",
            )
        if status == 404:
            raise SecuroError(
                f"{path} does not exist on this Securo instance.",
                status=status,
                hint="The URL may point at something that is not Securo, or a version without this endpoint.",
            )
        if status >= 400:
            raise SecuroError(
                f"Securo returned {status}: {self._detail(payload)}",
                status=status,
                hint="Check the instance logs if this repeats.",
            )
        if raw_response:
            return payload, response_headers
        if not payload:
            return None
        content_type = (response_headers.get("Content-Type") or "").lower()
        if "json" not in content_type:
            return payload.decode("utf-8", "replace")
        try:
            return json.loads(payload)
        except ValueError as error:
            raise SecuroError("Securo returned a response that is not JSON.", status=status) from error

    # -- auth -------------------------------------------------------------

    def login(self) -> str:
        """Exchange email + password for a bearer token. Returns the token."""
        if not (self._email and self._password):
            raise SecuroError(
                "No credential configured.",
                hint="Enter your Securo email and password, or an access token.",
            )
        payload = self._call(
            "POST",
            "/api/auth/login",
            form={"username": self._email, "password": self._password, "grant_type": "password"},
            retry_auth=False,
        )
        token = (payload or {}).get("access_token") if isinstance(payload, dict) else None
        if not token:
            raise SecuroError("Securo accepted the login but returned no token.")
        self._token = token
        self._logged_in = True
        return token

    def _ensure_token(self) -> None:
        if not self._token:
            self.login()

    # -- reads ------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        self._ensure_token()
        return self._call("GET", "/api/users/me") or {}

    def workspaces(self) -> list[dict[str, Any]]:
        self._ensure_token()
        data = self._call("GET", "/api/workspaces")
        return data if isinstance(data, list) else []

    def accounts(self) -> list[dict[str, Any]]:
        self._ensure_token()
        data = self._call("GET", "/api/accounts")
        rows = data if isinstance(data, list) else []
        return [
            {
                "id": row.get("id"),
                "name": row.get("display_name") or row.get("name") or "",
                "type": row.get("type") or "",
                "institution": row.get("institution_name") or "",
                "masked_number": row.get("masked_number") or "",
                "currency": row.get("currency") or "",
            }
            for row in rows
            if isinstance(row, dict)
        ]

    def transactions(self, from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]:
        """Every transaction in the window, across pages.

        Securo pages this endpoint (``limit`` caps at 500), so a year of charges
        is several calls. A short page ends the walk; ``total`` is used as a
        safety net so a misbehaving page cannot loop forever.
        """
        self._ensure_token()
        collected: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_PAGES:
            params = [
                ("from", from_date),
                ("to", to_date),
                ("page", page),
                ("limit", PAGE_SIZE),
                ("sort_by", "date"),
                ("sort_dir", "desc"),
            ]
            data = self._call("GET", "/api/transactions", params=params)
            if isinstance(data, list):
                collected.extend(row for row in data if isinstance(row, dict))
                break
            if not isinstance(data, dict):
                break
            items = data.get("items") or []
            collected.extend(row for row in items if isinstance(row, dict))
            total = data.get("total")
            if not items:
                break
            if isinstance(total, int) and len(collected) >= total:
                break
            if len(items) < PAGE_SIZE and not isinstance(total, int):
                break
            page += 1
        return collected

    def attachments(self, transaction_id: str) -> list[dict[str, Any]]:
        self._ensure_token()
        data = self._call("GET", f"/api/transactions/{transaction_id}/attachments")
        rows = data if isinstance(data, list) else (data or {}).get("items") or []
        return [row for row in rows if isinstance(row, dict)]

    def download_attachment(self, transaction_id: str, attachment_id: str) -> tuple[str, str, bytes]:
        """Return (filename, content_type, bytes) for one attachment."""
        self._ensure_token()
        payload, headers = self._call(
            "GET",
            f"/api/transactions/{transaction_id}/attachments/{attachment_id}",
            raw_response=True,
        )
        return _filename_from_headers(headers), headers.get("Content-Type") or "application/octet-stream", payload

    # -- one-shot diagnostics --------------------------------------------

    def check(self) -> dict[str, Any]:
        """Prove the URL and credential work, without touching any other data."""
        user = self.me()
        workspaces = self.workspaces()
        accounts = self.accounts()
        return {
            "ok": True,
            "base_url": self.base_url,
            "email": user.get("email") or "",
            "workspace": (workspaces[0].get("name") if workspaces else ""),
            "workspaces": len(workspaces),
            "accounts": len(accounts),
            "auth": "token" if (self._token and not self._logged_in) else "password",
        }


def _filename_from_headers(headers: dict[str, str]) -> str:
    disposition = ""
    for key, value in headers.items():
        if key.lower() == "content-disposition":
            disposition = value or ""
            break
    if "filename*=" in disposition:
        encoded = disposition.split("filename*=", 1)[1].split(";", 1)[0].strip().strip("'\"")
        if "''" in encoded:
            encoded = encoded.split("''", 1)[1]
        return urllib.parse.unquote(encoded) or "receipt"
    if "filename=" in disposition:
        return disposition.split("filename=", 1)[1].split(";", 1)[0].strip().strip("\"'") or "receipt"
    return "receipt"


def client_for(settings: dict[str, Any], **overrides: Any) -> SecuroClient:
    """Build a client from plugin settings (with optional per-call overrides)."""
    values = {
        "base_url": settings.get("securo_url") or "",
        "token": settings.get("securo_token") or None,
        "email": settings.get("securo_email") or None,
        "password": settings.get("securo_password") or None,
        "workspace_id": settings.get("securo_workspace_id") or None,
    }
    values.update({key: value for key, value in overrides.items() if value is not None})
    if not (values["token"] or (values["email"] and values["password"])):
        raise SecuroError(
            "Securo is not connected yet.",
            hint="Add your Securo URL and either an access token or your email and password.",
        )
    return SecuroClient(
        values["base_url"],
        token=values["token"],
        email=values["email"],
        password=values["password"],
        workspace_id=values["workspace_id"],
        timeout=float(settings.get("securo_timeout") or DEFAULT_TIMEOUT),
    )
