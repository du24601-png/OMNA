"""Export, backup, restore, and empty the local library.

Backups are SQLite backup copies plus a manifest. They do not include the
extractor key. Restore checks the manifest before replacing either database,
then disables every agent and drops the saved key.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from zhiwo.adapters.kernel_client import KernelHandle, read_version
from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import FROZEN_KERNEL, schema_version, setting
from zhiwo.services.commit_gate import commit_lock
from zhiwo.services.deletion import resume_deletions
from zhiwo.services.publish import publish_memory
from zhiwo.services.runtime_settings import clear_saved_extractor

_MAX_ARCHIVE = 512 * 1024 * 1024
_KEEP_SETTINGS = {"schema_version", "embedding_model", "kernel_package"}
_APP = "zhiwo"


def export_archive(db_path, handle: KernelHandle) -> bytes:
    memories = _memories(db_path, handle)
    document = {"exported_at": _now(), "memories": memories}
    markdown = _markdown(memories)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("memories.json", json.dumps(document, ensure_ascii=False, indent=2))
        archive.writestr("memories.md", markdown)
    return buffer.getvalue()


def backup_archive(settings, handle: KernelHandle) -> bytes:
    with commit_lock:
        resume_deletions(settings.control_db, handle)
        _finish_publishes(settings.control_db, handle)
        control = _snapshot(settings.control_db)
        kernel = _snapshot(handle.db_path)
        manifest = {
            "app": _APP,
            "schema_version": schema_version(settings.control_db),
            "kernel_package": setting(settings.control_db, "kernel_package") or FROZEN_KERNEL,
            "created_at": _now(),
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr("control/zhiwo.db", control)
            archive.writestr("kernel/mnemosyne.db", kernel)
        return buffer.getvalue()


def restore_archive(settings, handle: KernelHandle, payload: bytes, request_id: str, reopen=None):
    digest = hashlib.sha256(payload).hexdigest()
    with commit_lock:
        prior = _operation(settings.control_db, request_id)
        if prior is not None and prior["payload_hash"] != digest:
            raise ApiError(409, "CONFLICT", "this request id was already used with a different backup")
        if prior is not None and prior["status"] == "completed" and prior["action"] == "restore":
            return settings, True
        staged = _unpack_backup(payload)
        _require_same_version(settings.control_db, staged["manifest"])
        _integrity(staged["control"])
        _integrity(staged["kernel"])
        previous_control = _snapshot(settings.control_db)
        previous_kernel = _snapshot(handle.db_path)
        _remember(settings.control_db, request_id, "restore", digest, "prepared")
        _release_kernel(handle)
        try:
            try:
                _copy_into(staged["control"], settings.control_db)
                _copy_into(staged["kernel"], handle.db_path)
                _revoke_agents(settings.control_db)
                updated = clear_saved_extractor(settings, require_setup=True)
                _remember(settings.control_db, request_id, "restore", digest, "completed")
            except Exception as exc:
                try:
                    _release_kernel(handle)
                    _copy_into(previous_control, settings.control_db)
                    _copy_into(previous_kernel, handle.db_path)
                except Exception as rollback_error:
                    raise ApiError(
                        500,
                        "UNAVAILABLE",
                        "恢复失败，且没能把原来的库放回去。请停止服务后，用你另外保存的备份恢复。",
                    ) from rollback_error
                raise ApiError(500, "UNAVAILABLE", "恢复没有完成。当前库已放回恢复前的状态。") from exc
            return updated, False
        finally:
            if reopen is not None:
                reopen()


def reset_library(settings, handle: KernelHandle, request_id: str, confirm: str):
    if confirm != "清空数据":
        raise ApiError(400, "VALIDATION_ERROR", "请输入「清空数据」后再清空。")
    digest = hashlib.sha256(confirm.encode("utf-8")).hexdigest()
    with commit_lock:
        prior = _operation(settings.control_db, request_id)
        if prior is not None and prior["action"] != "reset":
            raise ApiError(409, "CONFLICT", "this request id was already used for a different action")
        if prior is not None and prior["payload_hash"] != digest:
            raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
        if prior is not None and prior["status"] == "completed":
            return settings, True
        resume_deletions(settings.control_db, handle)
        connection = _connect(settings.control_db)
        try:
            rows = connection.execute(
                "SELECT kernel_id, kernel_session FROM memory_refs WHERE kernel_id IS NOT NULL AND kernel_id != ''"
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            from zhiwo.services.deletion import _discard

            _discard(handle, row)
        connection = _connect(settings.control_db)
        try:
            for table in (
                "profile_summary",
                "proposals",
                "import_jobs",
                "sources",
                "memory_refs",
                "operations",
                "access_events",
                "agent_commands",
                "agent_permissions",
                "agents",
            ):
                connection.execute(f"DELETE FROM {table}")
            connection.execute(
                "DELETE FROM settings WHERE key NOT IN ({})".format(",".join("?" for _ in _KEEP_SETTINGS)),
                tuple(_KEEP_SETTINGS),
            )
            connection.commit()
        finally:
            connection.close()
        persona = handle.db_path.parent / "persona.md"
        if persona.is_file():
            persona.unlink()
        updated = clear_saved_extractor(settings, require_setup=False)
        _remember(updated.control_db, request_id, "reset", digest, "completed")
        return updated, False


def _finish_publishes(db_path, handle: KernelHandle) -> None:
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT id, payload_json FROM operations
            WHERE status = 'prepared' AND action IN ('publish', 'update')
            """
        ).fetchall()
    finally:
        connection.close()
    for row in rows:
        if not row["payload_json"]:
            continue
        publish_memory(db_path, handle, row["id"], json.loads(row["payload_json"]))


def _memories(db_path, handle: KernelHandle) -> list[dict]:
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT memory_id, revision, kernel_id, kernel_session, kind, category, scope,
                   lifecycle, share_enabled, valid_until, source_refs, created_at
            FROM memory_refs
            WHERE lifecycle IN ('active', 'superseded')
            ORDER BY memory_id, revision
            """
        ).fetchall()
        sources = {
            row["id"]: row
            for row in connection.execute("SELECT id, kind, name FROM sources").fetchall()
        }
    finally:
        connection.close()
    grouped: dict[str, dict] = {}
    for row in rows:
        content = None
        if row["kernel_id"]:
            content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
        source_ids = _source_ids(row["source_refs"])
        linked = []
        for source_id in source_ids:
            source = sources.get(source_id)
            if source is None:
                linked.append({"id": source_id, "deleted": True})
            else:
                linked.append({"id": source_id, "kind": source["kind"], "name": source["name"]})
        version = {
            "revision": row["revision"],
            "lifecycle": row["lifecycle"],
            "content": content,
            "kind": row["kind"],
            "category": row["category"],
            "scope": row["scope"],
            "share_enabled": bool(row["share_enabled"]),
            "valid_until": row["valid_until"],
            "created_at": row["created_at"],
            "sources": linked,
        }
        grouped.setdefault(row["memory_id"], {"id": row["memory_id"], "versions": []})["versions"].append(version)
    return list(grouped.values())


def _markdown(memories: list[dict]) -> str:
    labels = {
        "identity": "身份",
        "goal": "目标",
        "preference": "偏好",
        "project": "项目",
        "event": "事件",
        "other": "其他",
    }
    lines = ["# OMNA 导出", ""]
    if not memories:
        lines.append("没有已确认的记忆。")
        return "\n".join(lines)
    for memory in memories:
        category = memory["versions"][-1]["category"] if memory["versions"] else "other"
        lines.append(f"## {labels.get(category, category)}")
        lines.append("")
        for version in memory["versions"]:
            state = "当前" if version["lifecycle"] == "active" else "历史"
            lines.append(f"### 版本 {version['revision']}（{state}）")
            lines.append("")
            lines.append(version["content"] or "正文暂时无法读取。")
            lines.append("")
            if version["sources"]:
                rendered = []
                for source in version["sources"]:
                    if source.get("deleted"):
                        rendered.append("来源已删除")
                    else:
                        rendered.append(source.get("name") or source.get("kind") or "来源")
                lines.append("来源：" + "、".join(rendered))
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _snapshot(db_path: Path) -> bytes:
    import tempfile

    source = sqlite3.connect(db_path)
    try:
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                target.close()
            return temporary.read_bytes()
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        source.close()


def _unpack_backup(payload: bytes) -> dict:
    if not payload or len(payload) > _MAX_ARCHIVE:
        raise ApiError(400, "VALIDATION_ERROR", "备份文件是空的，或超过了可以恢复的大小。")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ApiError(400, "VALIDATION_ERROR", "这不是一份可以恢复的备份。") from exc
    names = set(archive.namelist())
    required = {"manifest.json", "control/zhiwo.db", "kernel/mnemosyne.db"}
    if not required.issubset(names):
        raise ApiError(400, "VALIDATION_ERROR", "备份里缺少记忆库文件。")
    total = 0
    for info in archive.infolist():
        if info.filename.startswith("/") or ".." in Path(info.filename).parts:
            raise ApiError(400, "VALIDATION_ERROR", "这不是一份可以恢复的备份。")
        total += info.file_size
        if total > _MAX_ARCHIVE:
            raise ApiError(400, "VALIDATION_ERROR", "备份文件超过了可以恢复的大小。")
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", "备份说明无法读取。") from exc
    if not isinstance(manifest, dict):
        raise ApiError(400, "VALIDATION_ERROR", "备份说明无法读取。")
    return {
        "manifest": manifest,
        "control": archive.read("control/zhiwo.db"),
        "kernel": archive.read("kernel/mnemosyne.db"),
    }


def _require_same_version(db_path, manifest: dict) -> None:
    if manifest.get("app") != _APP:
        raise ApiError(409, "CONFLICT", "这份备份不是 OMNA 的记忆库。当前库没有改动。")
    if manifest.get("schema_version") != schema_version(db_path):
        raise ApiError(409, "CONFLICT", "这份备份的版本和当前记忆库不一致。当前库没有改动。")
    if manifest.get("kernel_package") != FROZEN_KERNEL:
        raise ApiError(409, "CONFLICT", "这份备份的版本和当前记忆库不一致。当前库没有改动。")


def _integrity(blob: bytes) -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = Path(handle.name)
    try:
        path.write_bytes(blob)
        connection = sqlite3.connect(path)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        if not row or row[0] != "ok":
            raise ApiError(400, "VALIDATION_ERROR", "备份文件没有通过完整性检查。当前库没有改动。")
    finally:
        path.unlink(missing_ok=True)


def _release_kernel(handle: KernelHandle) -> None:
    """Close the Kernel's open connections so Windows can replace its files."""
    import gc

    memory = getattr(handle, "memory", None)
    beam = getattr(memory, "beam", None) if memory is not None else None
    connections = []
    if memory is not None:
        connections.append(getattr(memory, "conn", None))
    if beam is not None:
        connections.append(getattr(beam, "conn", None))
    for connection in connections:
        if connection is None:
            continue
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        try:
            connection.close()
        except sqlite3.Error:
            pass
    if memory is not None:
        memory.conn = None
    if beam is not None:
        beam.conn = None
    import mnemosyne.core.beam as beam_module
    import mnemosyne.core.memory as memory_module

    for module in (memory_module, beam_module):
        local = getattr(module, "_thread_local", None)
        if local is not None and getattr(local, "conn", None) is not None:
            try:
                local.conn.close()
            except sqlite3.Error:
                pass
            local.conn = None
    gc.collect()
    for suffix in ("-wal", "-shm"):
        extra = handle.db_path.parent / f"{handle.db_path.name}{suffix}"
        if extra.exists():
            extra.unlink()


def _copy_into(blob: bytes, destination: Path) -> None:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = Path(handle.name)
    try:
        path.write_bytes(blob)
        source = sqlite3.connect(path)
        target = sqlite3.connect(destination, timeout=30)
        try:
            source.backup(target)
            try:
                target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        finally:
            source.close()
            target.close()
        import gc

        gc.collect()
        for suffix in ("-wal", "-shm"):
            extra = Path(str(destination) + suffix)
            if extra.exists():
                extra.unlink()
    finally:
        path.unlink(missing_ok=True)


def _revoke_agents(db_path) -> None:
    import os

    connection = _connect(db_path)
    try:
        now = _now()
        rows = connection.execute("SELECT id FROM agents").fetchall()
        for row in rows:
            digest = hashlib.sha256(os.urandom(32)).hexdigest()
            connection.execute(
                """
                UPDATE agents
                SET enabled = 0, credential_hash = ?, policy_version = policy_version + 1, updated_at = ?
                WHERE id = ?
                """,
                (digest, now, row["id"]),
            )
        connection.commit()
    finally:
        connection.close()


def _operation(db_path, request_id: str):
    connection = _connect(db_path)
    try:
        return connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
    finally:
        connection.close()


def _remember(db_path, request_id: str, action: str, digest: str, status: str) -> None:
    connection = _connect(db_path)
    try:
        now = _now()
        existing = connection.execute("SELECT id FROM operations WHERE id = ?", (request_id,)).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO operations (
                    id, action, target_id, base_revision, payload_hash, payload_json,
                    revision, kernel_id, status, error_code, created_at, updated_at
                ) VALUES (?, ?, NULL, NULL, ?, '{}', NULL, NULL, ?, NULL, ?, ?)
                """,
                (request_id, action, digest, status, now, now),
            )
        else:
            connection.execute(
                "UPDATE operations SET action = ?, payload_hash = ?, status = ?, updated_at = ? WHERE id = ?",
                (action, digest, status, now, request_id),
            )
        connection.commit()
    finally:
        connection.close()


def _source_ids(raw) -> list[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def _connect(db_path):
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
