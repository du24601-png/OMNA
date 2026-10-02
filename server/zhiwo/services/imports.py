"""Store a source and its candidates.

With a model configured, the model extracts candidates. Without one, a
Markdown file, a whitelisted agent instruction file, or pasted text that has
headings or list items is split by structure instead. Either way identical
candidates are merged and near matches are marked. Nothing in this module
publishes a memory.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from zhiwo.api.errors import ApiError
from zhiwo.config import Settings
from zhiwo.services.agent_files import read_agent_file
from zhiwo.services.extract import ExtractionFailed, extract_candidates, extractor_config
from zhiwo.services.split import dedupe, looks_structured, split_markdown

_MAX_BYTES = 1024 * 1024


def import_source(db_path, settings: Settings, request_id: str, payload: dict, *, existing=None) -> dict:
    """`existing` returns current memories as [{"id", "content"}] for dedupe."""
    if payload.get("kind") == "agent_file":
        if set(payload) - {"kind", "file_id"}:
            raise ApiError(400, "VALIDATION_ERROR", "an agent file import takes only file_id")
        name, text = read_agent_file(payload.get("file_id"))
        payload = {"kind": "file", "name": name, "text": text}
    text, source_kind, name, source_format = _decode(payload)
    remembered = _existing(settings, existing, source_format, text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT * FROM import_jobs WHERE id = ?", (request_id,)).fetchone()
        if existing is not None:
            source = connection.execute("SELECT * FROM sources WHERE id = ?", (existing["source_id"],)).fetchone()
            connection.rollback()
            if source["content_hash"] != digest:
                raise ApiError(409, "CONFLICT", "this request id was already used with a different source")
            return _view(connection, existing, source, source_format)
        source_id = str(uuid.uuid4())
        now = _now()
        connection.execute(
            """
            INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, source_kind, name, text, digest, now),
        )
        _run_extraction(connection, settings, source_id, request_id, text, now, structured=_structured(source_format, text), existing=remembered)
        connection.commit()
        job = connection.execute("SELECT * FROM import_jobs WHERE id = ?", (request_id,)).fetchone()
        source = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if job is None or source is None:
            raise ApiError(500, "KERNEL_UNAVAILABLE", "the source record was not stored")
        return _view(connection, job, source, source_format)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def retry_import(db_path, settings: Settings, job_id: str, *, existing=None) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            connection.rollback()
            raise ApiError(404, "NOT_FOUND", "import job not found")
        source = connection.execute("SELECT * FROM sources WHERE id = ?", (job["source_id"],)).fetchone()
        if job["status"] == "extracted":
            connection.rollback()
            return _view(connection, job, source, _format_of(source))
        source_format = _format_of(source)
        connection.rollback()
        remembered = _existing(settings, existing, source_format, source["content"])
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if job["status"] == "extracted":
            connection.rollback()
            return _view(connection, job, source, source_format)
        _run_extraction(
            connection,
            settings,
            source["id"],
            job_id,
            source["content"],
            _now(),
            replace=True,
            structured=_structured(source_format, source["content"]),
            existing=remembered,
        )
        connection.commit()
        job = connection.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        return _view(connection, job, source, _format_of(source))
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _run_extraction(
    connection,
    settings: Settings,
    source_id: str,
    job_id: str,
    text: str,
    now: str,
    replace: bool = False,
    *,
    structured: bool = False,
    existing: list[dict] | None = None,
):
    if not settings.extractor_configured and structured:
        proposals = split_markdown(text)
        status, error_code, config = "extracted", None, json.dumps({"splitter": "markdown"})
    elif not settings.extractor_configured:
        status, error_code, config = "extractor_unavailable", "MODEL_UNAVAILABLE", None
        proposals = []
    else:
        config = extractor_config(settings.extractor_base_url, settings.extractor_model)
        try:
            proposals = extract_candidates(
                text,
                settings.extractor_base_url,
                settings.extractor_model,
                settings.extractor_api_key,
            )
            status, error_code = "extracted", None
        except ExtractionFailed as exc:
            proposals = []
            status, error_code = "failed", exc.code
    if status == "extracted":
        proposals, merged = dedupe(proposals, existing or [])
        details = json.loads(config) if config else {}
        details["merged_duplicates"] = merged
        config = json.dumps(details, ensure_ascii=False)
    if replace:
        connection.execute(
            """
            UPDATE import_jobs
            SET status = ?, extractor_config = ?, error_code = ?
            WHERE id = ?
            """,
            (status, config, error_code, job_id),
        )
    else:
        connection.execute(
            """
            INSERT INTO import_jobs (id, source_id, status, extractor_config, error_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (job_id, source_id, status, config, error_code, now),
        )
    already = connection.execute("SELECT COUNT(*) FROM proposals WHERE job_id = ?", (job_id,)).fetchone()[0]
    if status == "extracted" and already == 0:
        for item in proposals:
            connection.execute(
                """
                INSERT INTO proposals (
                    id, origin, change_type, payload_json, evidence_json, status,
                    source_id, job_id, created_at
                ) VALUES (?, 'import', 'add', ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    json.dumps(_payload(item), ensure_ascii=False),
                    json.dumps({"text": item["evidence"], "source_id": source_id}, ensure_ascii=False),
                    source_id,
                    job_id,
                    now,
                ),
            )
    return status, error_code, config


def _payload(item: dict) -> dict:
    payload = {
        "content": item["content"],
        "kind": item["kind"],
        "category": item["category"],
        "scope": item["scope"],
    }
    if item.get("similar_to"):
        payload["similar_to"] = item["similar_to"]
    if item.get("merged"):
        payload["merged"] = item["merged"]
    return payload


def _structured(source_format: str, text: str) -> bool:
    return source_format == "markdown" or looks_structured(text)


def _existing(settings: Settings, existing, source_format: str, text: str) -> list[dict]:
    if existing is None:
        return []
    if not settings.extractor_configured and not _structured(source_format, text):
        return []
    return list(existing())


def _decode(payload: dict) -> tuple[str, str, str | None, str]:
    kind = payload.get("kind")
    name = payload.get("name")
    text = payload.get("text")
    encoded = payload.get("content_base64")
    if kind not in {"paste", "file"}:
        raise ApiError(400, "VALIDATION_ERROR", "kind must be paste, file or agent_file")
    if payload.get("file_id") is not None:
        raise ApiError(400, "VALIDATION_ERROR", "file_id is only for agent_file imports")
    if (text is None) == (encoded is None):
        raise ApiError(400, "VALIDATION_ERROR", "provide either text or content_base64")
    if encoded is not None:
        try:
            raw = base64.b64decode(encoded, validate=True)
            text = raw.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ApiError(400, "VALIDATION_ERROR", "file text must be valid UTF-8") from exc
    if not isinstance(text, str) or text == "":
        raise ApiError(400, "VALIDATION_ERROR", "source text is required")
    if len(text.encode("utf-8")) > _MAX_BYTES:
        raise ApiError(400, "VALIDATION_ERROR", "a source can contain at most 1 MiB")
    if kind == "paste":
        return text, "paste", name if isinstance(name, str) and name else None, "paste"
    if not isinstance(name, str):
        raise ApiError(400, "VALIDATION_ERROR", "a file import needs a .txt or .md name")
    lower = name.lower()
    if lower.endswith(".txt"):
        return text, "file", name, "txt"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return text, "file", name, "markdown"
    raise ApiError(400, "VALIDATION_ERROR", "only UTF-8 TXT and Markdown files can be imported")


def _format_of(source) -> str:
    if source["kind"] == "paste":
        return "paste"
    name = (source["name"] or "").lower()
    if name.endswith(".txt"):
        return "txt"
    return "markdown"


def _view(connection, job, source, source_format: str) -> dict:
    rows = connection.execute(
        "SELECT id, status, payload_json, evidence_json, source_id FROM proposals WHERE job_id = ?",
        (job["id"],),
    ).fetchall()
    proposals = []
    for row in rows:
        proposals.append(
            {
                "id": row["id"],
                "status": row["status"],
                "source_id": row["source_id"],
                "payload": json.loads(row["payload_json"]),
                "evidence": json.loads(row["evidence_json"]),
            }
        )
    details = {}
    if job["extractor_config"]:
        try:
            details = json.loads(job["extractor_config"])
        except json.JSONDecodeError:
            details = {}
    return {
        "source_id": source["id"],
        "job_id": job["id"],
        "method": "split" if details.get("splitter") else "model" if job["extractor_config"] else None,
        "merged_duplicates": int(details.get("merged_duplicates") or 0),
        "kind": source["kind"],
        "format": source_format,
        "name": source["name"],
        "status": job["status"],
        "error_code": job["error_code"],
        "proposals": proposals,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
