"""Explicit attachment writes, kept separate from the read-only scan client."""
from __future__ import annotations

import json
import mimetypes
import os
import stat
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from securo_client import SecuroClient, SecuroError

DEFAULT_MAX_BYTES = 25 * 1024 * 1024


def request_error(status: int | None) -> SecuroError:
    """Do not echo an untrusted server body, which may contain credentials."""
    messages: dict[int | None, tuple[str, str]] = {
        401: ("Securo rejected the credential.", "Sign in again with your Securo email and password, or replace the access token."),
        403: ("Securo refused attachment permission for this user.", "Check workspace membership and permission to add attachments."),
        404: ("The transaction or attachment endpoint was not found in Securo.", "Rescan and select the transaction again; check the Securo URL and version."),
        413: ("Securo says the attachment is too large.", "Use a smaller receipt or check the Securo and reverse-proxy upload limits."),
    }
    message, hint = messages.get(status, (
        f"Securo returned {status} during the attachment request." if status else "Could not complete the Securo attachment request.",
        "Check connectivity and instance logs. Check the transaction in Securo before retrying; an upload may already have completed.",
    ))
    return SecuroError(message, status=status, hint=hint)


def validate_file(path: str | Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[Path, int]:
    """Check a regular readable file without allocating its contents."""
    try:
        source = Path(path).expanduser()
        info = source.stat()
        if not stat.S_ISREG(info.st_mode):
            raise SecuroError("The receipt path is not a regular file.", hint="Choose a receipt file, not a folder or device.")
        if not info.st_size:
            raise SecuroError("The receipt file is empty.", hint="Download the receipt again and choose the completed file.")
        if info.st_size > max_bytes:
            raise SecuroError("The receipt file is too large.", status=413, hint=f"Choose a file no larger than {max_bytes} bytes.")
        with source.open("rb") as handle:
            handle.read(1)
        return source, info.st_size
    except (OSError, ValueError, TypeError) as error:
        raise SecuroError("The receipt file does not exist or is not readable.", hint="Check the local path and file permissions.") from error


class SecuroWriter:
    """Upload only on explicit invocation; never retry a possibly completed write."""

    def __init__(self, client: SecuroClient, *, max_bytes: int = DEFAULT_MAX_BYTES):
        self.client = client
        self.max_bytes = max_bytes

    def upload_attachment(self, transaction_id: str, path: str | Path, filename: str | None = None) -> dict:
        source, _ = validate_file(path, max_bytes=self.max_bytes)
        name = filename if filename is not None else source.name
        if not isinstance(name, str) or not name or any(ord(char) < 32 or ord(char) == 127 for char in name) or "/" in name or "\\" in name:
            raise SecuroError("The attachment filename is not safe.", hint="Use a plain filename without folders or control characters.")
        try:
            with source.open("rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise SecuroError("The receipt path is not a regular file.", hint="Choose a receipt file.")
                payload = handle.read(self.max_bytes + 1)
        except OSError as error:
            raise SecuroError("The receipt file is not readable.", hint="Check the local path and file permissions.") from error
        if not payload or len(payload) > self.max_bytes:
            raise SecuroError("The receipt file is empty or too large.", hint="Choose a nonempty receipt within the upload size limit.")
        boundary = "receipt-ledger-" + uuid4().hex
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        escaped_name = name.replace("\\", "\\\\").replace('"', '\\"')
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{escaped_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + payload + f"\r\n--{boundary}--\r\n".encode()
        try:
            self.client._ensure_token()
            status, _, response = self.client._transport(
                "POST", self.client._url(f"/api/transactions/{quote(transaction_id, safe='')}/attachments"),
                self.client._headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}),
                body, self.client.timeout,
            )
        except (SecuroError, OSError) as error:
            raise request_error(getattr(error, "status", None)) from error
        if not 200 <= status < 300:
            raise request_error(status)
        try:
            result = json.loads(response)
            attachment_id = result["id"]
            if not isinstance(attachment_id, (str, int)) or not attachment_id:
                raise ValueError("missing attachment id")
            if self.client._token and self.client._token in str(attachment_id):
                raise ValueError("unsafe attachment metadata")
        except (ValueError, TypeError, KeyError) as error:
            raise SecuroError("Securo returned invalid attachment metadata.", hint="Check the transaction in Securo before retrying; the upload may have completed.") from error
        return {"id": attachment_id, "filename": name, "size": len(payload)}
