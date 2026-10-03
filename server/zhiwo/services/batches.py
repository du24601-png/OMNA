"""Batches: accept the plain additions of one batch, or undo a whole batch.

A batch is either one import job (its proposals share `job_id`) or one
"organize" session: the owner asks a connected agent to turn its own
instruction file into memories, and for the next hour that agent's new
additions carry the session id as `payload.batch_id`.

Accepting goes through the existing `decide_proposal`, one proposal at a
time, with a request id derived from the batch and the proposal, so a
repeated call resumes instead of publishing twice. Only plain additions
are accepted here; updates, near duplicates, over-long text and proposals
without evidence stay pending for one-by-one review. Undo deletes, through
the existing permanent-delete flow, every new memory the batch produced,
and refuses the whole batch if any of them has been changed since.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from zhiwo.adapters.kernel_client import KernelHandle
from zhiwo.api.errors import ApiError
from zhiwo.services.commit_gate import commit_lock
from zhiwo.services.deletion import delete_memory
from zhiwo.services.organize import SESSION_PREFIX
from zhiwo.services.review import decide_proposal
from zhiwo.services.split import SIMILAR_THRESHOLD, normalize, overlap

SESSION_MINUTES = 60
UNDO_PREFIX = "undo_batch:"
MAX_CONTENT = 2000
# It starts with a read so that one sentence also verifies a new connection:
# only a delivered read marks a client verified, a proposal does not.
ORGANIZE_PROMPT = (
    "请先查一下 OMNA 里我的偏好，再读一下你的全局说明文件，把其中关于我本人的内容（身份、偏好、目标、正在做的项目）"
    "整理成记忆，逐条用 propose_memory 提交给 OMNA。只对某个代码库成立的规则、命令和路径不要提交。"
)
_KEYS = uuid.UUID("2c1f8d4e-7a9b-4c3d-8e5f-0a1b2c3d4e5f")


def start_organize(db_path, agent_id: str, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            agent = connection.execute("SELECT id, enabled FROM agents WHERE id = ?", (agent_id,)).fetchone()
            if agent is None:
                raise ApiError(404, "NOT_FOUND", "agent not found")
            permissions = connection.execute(
                "SELECT allowed_tools, propose_categories FROM agent_permissions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            tools = json.loads(permissions["allowed_tools"]) if permissions else []
            propose = json.loads(permissions["propose_categories"] or "[]") if permissions else []
            if not agent["enabled"] or "propose_memory" not in tools or not propose:
                raise ApiError(409, "CONFLICT", "这个连接现在不能提议记忆。先允许它访问，并在权限里选「可提议修改」。")
            session = {
                "batch_id": str(uuid.uuid4()),
                "started_at": current.isoformat(),
                "expires_at": (current + timedelta(minutes=SESSION_MINUTES)).isoformat(),
            }
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (SESSION_PREFIX + agent_id, json.dumps(session)),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
    return {**session, "agent_id": agent_id, "prompt": ORGANIZE_PROMPT}


def batch_summary(db_path, batch_id: str) -> dict:
    connection = _connect(db_path)
    try:
        members = _members(connection, batch_id)
        if not members:
            raise ApiError(404, "NOT_FOUND", "batch not found")
        produced = _produced(connection, members)
    finally:
        connection.close()
    pending = [row for row in members if row["status"] == "pending"]
    additions = [row for row in pending if row["change_type"] == "add"]
    plain = [row for row in additions if _skip_reason(row, set()) is None]
    modified = [item for item in produced if item["state"] == "modified"]
    return {
        "batch_id": batch_id,
        "kind": "organize" if members[0]["job_id"] != batch_id else "import",
        "agent_id": members[0]["agent_id"],
        "pending": len(pending),
        "pending_additions": len(additions),
        "plain_additions": len(plain),
        "remembered": len([item for item in produced if item["state"] != "deleted"]),
        "undoable": bool(produced) and not modified and any(item["state"] == "current" for item in produced),
        "changed_since": len(modified),
    }


def accept_additions(db_path, handle: KernelHandle, batch_id: str, exclude: list[str] | None, existing) -> dict:
    """`existing` returns current memories as [{"id", "content"}]."""
    skipped_ids = set(exclude or [])
    connection = _connect(db_path)
    try:
        members = _members(connection, batch_id)
    finally:
        connection.close()
    if not members:
        raise ApiError(404, "NOT_FOUND", "batch not found")
    current = list(existing())
    remembered = {normalize(item["content"]) for item in current if isinstance(item.get("content"), str)}
    results = []
    for row in members:
        request_id = str(uuid.uuid5(_KEYS, f"{batch_id}:{row['id']}:accept"))
        resumed = row["status"] == "publishing" and row["operation_id"] == request_id
        if not resumed and row["status"] != "pending":
            continue
        reason = None if resumed else _skip_reason(row, skipped_ids)
        content = json.loads(row["payload_json"]).get("content") or ""
        if reason is None and not resumed and normalize(content) in remembered:
            reason = "duplicate"
        if reason is None and not resumed and any(overlap(content, item["content"]) >= SIMILAR_THRESHOLD for item in current if len(item.get("content") or "") >= 8):
            reason = "similar"
        if reason is not None:
            results.append({"proposal_id": row["id"], "status": "skipped", "reason": reason})
            continue
        try:
            published = decide_proposal(db_path, handle, row["id"], request_id, {"decision": "accept"})
            results.append({"proposal_id": row["id"], "status": "accepted", "memory_id": published.get("memory_id")})
            remembered.add(normalize(content))
            current.append({"id": published.get("memory_id"), "content": content})
        except ApiError as exc:
            results.append({"proposal_id": row["id"], "status": "failed", "code": exc.code, "message": exc.message})
    return {
        "batch_id": batch_id,
        "accepted": sum(1 for item in results if item["status"] == "accepted"),
        "skipped": sum(1 for item in results if item["status"] == "skipped"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }


def undo_batch(db_path, handle: KernelHandle, batch_id: str, confirm: object) -> dict:
    """Delete every new memory the batch produced, or none if any was changed.

    The first deletion also removes the import's source and its remaining
    candidates, so the list of memories to delete is written down before
    deleting. A retry after a crash finishes that list.
    """
    if confirm is not True:
        raise ApiError(400, "VALIDATION_ERROR", "撤销会永久删除这批新增的记忆，需要 confirm: true。")
    plan_key = UNDO_PREFIX + str(batch_id)
    connection = _connect(db_path)
    try:
        members = _members(connection, batch_id)
        planned = connection.execute("SELECT value FROM settings WHERE key = ?", (plan_key,)).fetchone()
        if members:
            produced = _produced(connection, members)
        elif planned is not None:
            produced = [{"memory_id": memory_id, "state": "current"} for memory_id in json.loads(planned[0])]
        else:
            raise ApiError(404, "NOT_FOUND", "batch not found")
    finally:
        connection.close()
    modified = [item for item in produced if item["state"] == "modified"]
    if modified:
        raise ApiError(409, "CONFLICT", f"这批里有 {len(modified)} 条记忆之后又改过，整批撤销会丢掉这些修改，所以没有撤销。可以在记忆页逐条删除。")
    targets = [item["memory_id"] for item in produced if item["state"] == "current"]
    _put(db_path, plan_key, json.dumps(targets))
    deleted = []
    for memory_id in targets:
        request_id = str(uuid.uuid5(_KEYS, f"{batch_id}:{memory_id}:undo"))
        if not _exists(db_path, memory_id):
            continue
        try:
            delete_memory(db_path, handle, memory_id, request_id)
        except ApiError as exc:
            if exc.code != "NOT_FOUND":
                raise
            continue
        deleted.append(memory_id)
    _drop(db_path, plan_key)
    return {"batch_id": batch_id, "deleted": len(deleted), "memory_ids": deleted}


def _exists(db_path, memory_id: str) -> bool:
    connection = _connect(db_path)
    try:
        return connection.execute("SELECT 1 FROM memory_refs WHERE memory_id = ? LIMIT 1", (memory_id,)).fetchone() is not None
    finally:
        connection.close()


def _put(db_path, key: str, value: str) -> None:
    connection = _connect(db_path)
    try:
        connection.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        connection.commit()
    finally:
        connection.close()


def _drop(db_path, key: str) -> None:
    connection = _connect(db_path)
    try:
        connection.execute("DELETE FROM settings WHERE key = ?", (key,))
        connection.commit()
    finally:
        connection.close()


def _members(connection, batch_id: str) -> list[sqlite3.Row]:
    if not isinstance(batch_id, str) or not batch_id:
        return []
    return connection.execute(
        """
        SELECT * FROM proposals
        WHERE job_id = ? OR json_extract(payload_json, '$.batch_id') = ?
        ORDER BY created_at, id
        """,
        (batch_id, batch_id),
    ).fetchall()


def _produced(connection, members) -> list[dict]:
    """New memories this batch published, and whether each is unchanged since."""
    produced = []
    for row in members:
        if row["status"] != "accepted" or row["change_type"] != "add" or not row["operation_id"]:
            continue
        made = connection.execute(
            "SELECT memory_id, revision FROM memory_refs WHERE operation_id = ?",
            (row["operation_id"],),
        ).fetchone()
        if made is None:
            produced.append({"proposal_id": row["id"], "memory_id": None, "state": "deleted"})
            continue
        active = connection.execute(
            "SELECT revision FROM memory_refs WHERE memory_id = ? AND lifecycle = 'active'",
            (made["memory_id"],),
        ).fetchone()
        if active is None:
            state = "deleted"
        else:
            state = "current" if active["revision"] == made["revision"] else "modified"
        produced.append({"proposal_id": row["id"], "memory_id": made["memory_id"], "state": state})
    return produced


def _skip_reason(row, excluded: set[str]) -> str | None:
    if row["id"] in excluded:
        return "excluded"
    if row["change_type"] != "add" or row["target_id"]:
        return "update"
    payload = json.loads(row["payload_json"])
    if payload.get("similar_to"):
        return "similar"
    if len(payload.get("content") or "") > MAX_CONTENT:
        return "long"
    try:
        evidence = json.loads(row["evidence_json"]).get("text")
    except (json.JSONDecodeError, AttributeError):
        evidence = None
    if not isinstance(evidence, str) or not evidence.strip():
        return "evidence"
    return None


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection
