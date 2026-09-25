"""Owner credential check. The token is not written to the database or logs."""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

UNAUTHENTICATED = {
    "error": {
        "code": "UNAUTHENTICATED",
        "message": "owner credential rejected",
        "retryable": False,
    }
}


class OwnerAuthError(Exception):
    """The request did not present the Owner credential."""


def require_owner(request: Request) -> None:
    presented = _bearer(request.headers.get("authorization"))
    expected = request.app.state.settings.owner_credential
    if not _matches(presented, expected):
        raise OwnerAuthError()


def owner_auth_error(_request: Request, _exc: OwnerAuthError) -> JSONResponse:
    return JSONResponse(status_code=401, content=UNAUTHENTICATED)


def install_redaction(*secrets: str) -> None:
    for secret in secrets:
        if secret:
            logging.getLogger().addFilter(_Redact(secret))


def _bearer(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _matches(presented: str, expected: str) -> bool:
    if not presented or not expected:
        return False
    left = hashlib.sha256(presented.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


class _Redact(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secret in record.getMessage():
            record.msg = record.getMessage().replace(self._secret, "[redacted]")
            record.args = ()
        return True
