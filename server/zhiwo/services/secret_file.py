"""Store the extractor key with Windows DPAPI.

The key is not written to zhiwo.db, logs, or API responses. A missing file
means the process still uses the environment, until a restore asks for a
new key.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

KEY_NAME = "extractor.key"
_UI_FORBIDDEN = 0x01


class SecretFileError(RuntimeError):
    """The key file could not be protected or read."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def key_path(data_dir: Path) -> Path:
    return data_dir / KEY_NAME


def key_saved(data_dir: Path) -> bool:
    return key_path(data_dir).is_file()


def write_key(data_dir: Path, secret: str) -> None:
    if not isinstance(secret, str) or not secret:
        raise SecretFileError("extractor key is empty")
    protected = _protect(secret.encode("utf-8"))
    path = key_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".key.tmp")
    temporary.write_bytes(protected)
    temporary.replace(path)


def read_key(data_dir: Path) -> str | None:
    path = key_path(data_dir)
    if not path.is_file():
        return None
    try:
        return _unprotect(path.read_bytes()).decode("utf-8")
    except (OSError, UnicodeDecodeError, SecretFileError) as exc:
        raise SecretFileError("saved extractor key could not be read") from exc


def delete_key(data_dir: Path) -> None:
    path = key_path(data_dir)
    if path.is_file():
        path.unlink()


def _protect(data: bytes) -> bytes:
    return _crypt(data, encrypt=True)


def _unprotect(data: bytes) -> bytes:
    return _crypt(data, encrypt=False)


def _crypt(data: bytes, *, encrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise SecretFileError("extractor keys are stored with Windows DPAPI")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buffer = ctypes.create_string_buffer(data)
    incoming = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    function = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    ok = function(ctypes.byref(incoming), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing))
    if not ok:
        raise SecretFileError("Windows DPAPI failed")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(outgoing.pbData)
