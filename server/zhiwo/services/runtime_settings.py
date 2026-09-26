"""Extractor settings saved by the owner.

The address and model name live in the settings table. The key lives in a
DPAPI file. Until the owner saves one, the process keeps using environment
variables. A restore sets extractor_requires_setup so the old environment
key is not treated as configured.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

from zhiwo.api.errors import ApiError
from zhiwo.config import Settings, load_settings
from zhiwo.repositories.migrate import drop_setting, put_setting, setting
from zhiwo.services.secret_file import SecretFileError, delete_key, key_saved, read_key, write_key

URL_KEY = "extractor_base_url"
MODEL_KEY = "extractor_model"
SETUP_KEY = "extractor_requires_setup"


def resolve_extractor(settings: Settings) -> Settings:
    url = setting(settings.control_db, URL_KEY)
    model = setting(settings.control_db, MODEL_KEY)
    setup = setting(settings.control_db, SETUP_KEY) == "1"
    saved = _saved_key(settings)
    if url is None and model is None and saved is None and not setup:
        return settings
    if setup and saved is None:
        return _replace(settings, url or "", model or "", "")
    return _replace(
        settings,
        url if url is not None else settings.extractor_base_url,
        model if model is not None else settings.extractor_model,
        saved if saved is not None else settings.extractor_api_key,
    )


def public_settings(settings: Settings) -> dict:
    return {
        "data_dir": str(settings.data_dir),
        "extractor": {
            "base_url": settings.extractor_base_url,
            "model": settings.extractor_model,
            "key_saved": key_saved(settings.data_dir),
            "configured": settings.extractor_configured,
        },
        "schema_version": _schema(settings),
        "kernel_package": setting(settings.control_db, "kernel_package"),
    }


def save_extractor(settings: Settings, request_id: str, base_url: str, model: str, api_key: str | None) -> Settings:
    cleaned_url = _optional_text(base_url, "extractor address")
    cleaned_model = _optional_text(model, "extractor model")
    provided = api_key if isinstance(api_key, str) and api_key else ""
    digest = hashlib.sha256(
        json.dumps(
            {"base_url": cleaned_url, "model": cleaned_model, "key": provided},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    prior = _operation(settings.control_db, request_id)
    if prior is not None and prior["action"] != "settings":
        raise ApiError(409, "CONFLICT", "this request id was already used for a different action")
    if prior is not None and prior["payload_hash"] != digest:
        raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
    if prior is not None and prior["status"] == "completed":
        return resolve_extractor(settings)
    updated = update_extractor(settings, cleaned_url, cleaned_model, api_key)
    _remember(settings.control_db, request_id, digest)
    return updated


def update_extractor(settings: Settings, base_url: str, model: str, api_key: str | None) -> Settings:
    cleaned_url = _optional_text(base_url, "extractor address")
    cleaned_model = _optional_text(model, "extractor model")
    put_setting(settings.control_db, URL_KEY, cleaned_url)
    put_setting(settings.control_db, MODEL_KEY, cleaned_model)
    if isinstance(api_key, str) and api_key:
        try:
            write_key(settings.data_dir, api_key)
        except SecretFileError as exc:
            raise ApiError(503, "MODEL_UNAVAILABLE", "the extractor key could not be saved", retryable=True) from exc
        drop_setting(settings.control_db, SETUP_KEY)
        secret = api_key
    else:
        saved = _saved_key(settings)
        if setting(settings.control_db, SETUP_KEY) == "1" and saved is None:
            secret = ""
        elif saved is not None:
            secret = saved
        else:
            secret = settings.extractor_api_key
    return _replace(settings, cleaned_url, cleaned_model, secret)


def clear_saved_extractor(settings: Settings, *, require_setup: bool) -> Settings:
    drop_setting(settings.control_db, URL_KEY)
    drop_setting(settings.control_db, MODEL_KEY)
    delete_key(settings.data_dir)
    if require_setup:
        put_setting(settings.control_db, SETUP_KEY, "1")
        return _replace(settings, "", "", "")
    drop_setting(settings.control_db, SETUP_KEY)
    env = load_settings()
    return _replace(settings, env.extractor_base_url, env.extractor_model, env.extractor_api_key)


def test_extractor(settings: Settings) -> dict:
    if not settings.extractor_configured:
        raise ApiError(503, "MODEL_UNAVAILABLE", "extractor is not configured", retryable=True)
    body = {
        "model": settings.extractor_model,
        "temperature": 0,
        "messages": [{"role": "user", "content": "Reply with pong."}],
    }
    request = urllib.request.Request(
        settings.extractor_base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.extractor_api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except TimeoutError as exc:
        raise ApiError(503, "TIMEOUT", "extractor timed out", retryable=True) from exc
    except urllib.error.HTTPError as exc:
        raise ApiError(503, "MODEL_UNAVAILABLE", "extractor request failed", retryable=True) from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ApiError(503, "TIMEOUT", "extractor timed out", retryable=True) from exc
        raise ApiError(503, "MODEL_UNAVAILABLE", "extractor request failed", retryable=True) from exc
    except OSError as exc:
        raise ApiError(503, "MODEL_UNAVAILABLE", "extractor request failed", retryable=True) from exc
    try:
        payload = json.loads(raw)
        if not payload["choices"]:
            raise KeyError("choices")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ApiError(502, "VALIDATION_ERROR", "extractor returned an invalid format") from exc
    return {"ok": True}


def _saved_key(settings: Settings) -> str | None:
    if not key_saved(settings.data_dir):
        return None
    try:
        return read_key(settings.data_dir)
    except SecretFileError as exc:
        raise ApiError(503, "MODEL_UNAVAILABLE", "the saved extractor key could not be read", retryable=True) from exc


def _replace(settings: Settings, base_url: str, model: str, api_key: str) -> Settings:
    return Settings(
        data_dir=settings.data_dir,
        owner_credential=settings.owner_credential,
        connect_only=settings.connect_only,
        fastembed_cache=settings.fastembed_cache,
        extractor_base_url=base_url,
        extractor_model=model,
        extractor_api_key=api_key,
        test_mode=settings.test_mode,
        host=settings.host,
    )


def _optional_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", f"{label} is invalid")
    text = value.strip()
    if len(text) > 500:
        raise ApiError(400, "VALIDATION_ERROR", f"{label} is too long")
    return text


def _operation(db_path, request_id: str):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
    finally:
        connection.close()


def _remember(db_path, request_id: str, digest: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        existing = connection.execute("SELECT id FROM operations WHERE id = ?", (request_id,)).fetchone()
        public = json.dumps({"key": "not stored"}, ensure_ascii=False)
        if existing is None:
            connection.execute(
                """
                INSERT INTO operations (
                    id, action, target_id, base_revision, payload_hash, payload_json,
                    revision, kernel_id, status, error_code, created_at, updated_at
                ) VALUES (?, 'settings', NULL, NULL, ?, ?, NULL, NULL, 'completed', NULL, ?, ?)
                """,
                (request_id, digest, public, now, now),
            )
        else:
            connection.execute(
                """
                UPDATE operations
                SET payload_hash = ?, payload_json = ?, status = 'completed', updated_at = ?
                WHERE id = ?
                """,
                (digest, public, now, request_id),
            )
        connection.commit()
    finally:
        connection.close()


def _schema(settings: Settings) -> int:
    from zhiwo.repositories.migrate import schema_version

    return schema_version(settings.control_db)
