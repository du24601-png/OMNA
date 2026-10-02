"""Owner review. Every approval publishes through publish_memory.

Reject does not write the Kernel. A repeated review is compared with the
stored intent, including whether share state and expiry were submitted.
The same intent returns the original result. A different intent returns
CONFLICT and leaves official memory unchanged.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import KernelHandle
from zhiwo.api.errors import ApiError
from zhiwo.services.memories import requester_labels, requester_of
from zhiwo.services.publish import (
    VERSION_CONFLICT,
    canonical_record,
    operation_result,
    publish_lock,
    publish_memory,
)

_DECISIONS = {"accept", "update", "keep_both", "edit", "reject"}
_DECISION_LABEL = {
    "accept": "通过",
    "update": "更新",
    "keep_both": "两者保留",
    "edit": "编辑后保存",
    "reject": "拒绝",
}


def list_proposals(db_path, *, demo: bool = False, status: str | None = None, since: str | None = None) -> dict:
    since_at = _since(since)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        sql = """
            SELECT proposals.*, sources.kind AS source_kind, sources.name AS source_name
            FROM proposals
            JOIN sources ON sources.id = proposals.source_id
        """
        values: list[str] = []
        clauses: list[str] = []
        if status:
            clauses.append("proposals.status = ?")
            values.append(status)
        if since_at:
            clauses.append("proposals.created_at > ?")
            values.append(since_at)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY proposals.created_at, proposals.id"
        rows = connection.execute(sql, values).fetchall()
    finally:
        connection.close()
    agents, clients = requester_labels(db_path)
    return {"proposals": [_proposal_view(row, demo=demo, agents=agents, clients=clients) for row in rows]}


def _since(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", "since must be an ISO 8601 time") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def get_proposal(db_path, proposal_id: str, *, demo: bool = False) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT proposals.*, sources.kind AS source_kind, sources.name AS source_name
            FROM proposals
            JOIN sources ON sources.id = proposals.source_id
            WHERE proposals.id = ?
            """,
            (proposal_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "proposal not found")
    agents, clients = requester_labels(db_path)
    return _proposal_view(row, demo=demo, agents=agents, clients=clients)


def decide_proposal(db_path, handle: KernelHandle, proposal_id: str, request_id: str, body: dict) -> dict:
    with publish_lock():
        return _decide(db_path, handle, proposal_id, request_id, body)


def _decide(db_path, handle: KernelHandle, proposal_id: str, request_id: str, body: dict) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        proposal = connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if proposal is None:
            raise ApiError(404, "NOT_FOUND", "proposal not found")
        decision = body.get("decision")
        if decision not in _DECISIONS:
            raise ApiError(400, "VALIDATION_ERROR", "decision is not supported")
        incoming = _intent(body)
        if proposal["status"] != "pending":
            return _replay(db_path, handle, connection, proposal, incoming)
        if decision == "reject":
            return _reject(connection, proposal, incoming)
        record = canonical_record(_review_record(connection, proposal, body, decision))
        change_type = "update" if record["target_id"] else "add"
        cursor = connection.execute(
            """
            UPDATE proposals
            SET status = 'publishing',
                decision = ?,
                operation_id = ?,
                change_type = ?,
                target_id = ?,
                base_revision = ?,
                intent_json = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                decision,
                request_id,
                change_type,
                record["target_id"],
                record["base_revision"],
                _dump(incoming),
                proposal_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ApiError(409, "CONFLICT", "this proposal cannot be reviewed again")
        connection.commit()
        try:
            published = publish_memory(
                db_path,
                handle,
                request_id,
                record,
                finalize=lambda control: _accept(control, proposal_id, decision, request_id),
            )
        except ApiError as exc:
            if exc.status in {400, 404, 409}:
                _reopen(connection, proposal_id, request_id, fail_operation=exc.message == VERSION_CONFLICT)
            raise
        published["proposal_id"] = proposal_id
        published["decision"] = decision
        return published
    finally:
        connection.close()


def _resume(db_path, handle: KernelHandle, connection: sqlite3.Connection, proposal) -> dict:
    operation = connection.execute(
        "SELECT * FROM operations WHERE id = ?",
        (proposal["operation_id"],),
    ).fetchone()
    if operation is None or not operation["payload_json"]:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "the review operation has no stored payload", retryable=True)
    connection.commit()
    if operation["status"] == "completed":
        _accept(connection, proposal["id"], proposal["decision"], operation["id"])
        connection.commit()
        published = operation_result(db_path, handle, operation["id"])
        return _decision_view(proposal, published)
    stored = json.loads(operation["payload_json"])
    decision = proposal["decision"]
    published = publish_memory(
        db_path,
        handle,
        operation["id"],
        stored,
        finalize=lambda control: _accept(control, proposal["id"], decision, operation["id"]),
    )
    published["proposal_id"] = proposal["id"]
    published["decision"] = decision
    return published


def _replay(db_path, handle: KernelHandle, connection: sqlite3.Connection, proposal, incoming: dict) -> dict:
    if not _same_intent(_stored_intent(proposal), incoming):
        raise ApiError(409, "CONFLICT", _replay_message(proposal))
    if proposal["status"] == "rejected":
        return _decision_view(proposal, None)
    if proposal["status"] in {"publishing", "failed"} and proposal["operation_id"]:
        return _resume(db_path, handle, connection, proposal)
    if proposal["status"] == "accepted" and proposal["operation_id"]:
        published = operation_result(db_path, handle, proposal["operation_id"])
        return _decision_view(proposal, published)
    raise ApiError(409, "CONFLICT", _replay_message(proposal))


def _reject(connection: sqlite3.Connection, proposal, intent: dict) -> dict:
    cursor = connection.execute(
        """
        UPDATE proposals
        SET status = 'rejected', decision = 'reject', intent_json = ?
        WHERE id = ? AND status = 'pending'
        """,
        (_dump(intent), proposal["id"]),
    )
    if cursor.rowcount != 1:
        raise ApiError(409, "CONFLICT", "this proposal cannot be reviewed again")
    connection.commit()
    return {
        "proposal_id": proposal["id"],
        "status": "rejected",
        "decision": "reject",
    }


def _review_record(connection: sqlite3.Connection, proposal, body: dict, decision: str) -> dict:
    stored = json.loads(proposal["payload_json"])
    content = stored.get("content")
    kind = stored.get("kind")
    category = stored.get("category")
    scope = stored.get("scope")
    target_id = None
    base_revision = None
    if decision == "edit":
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ApiError(400, "VALIDATION_ERROR", "edited content is required")
        kind = body.get("kind") or kind
        category = body.get("category") or category
        if body.get("scope"):
            scope = body.get("scope")
        target_id = body.get("target_id")
        base_revision = body.get("base_revision")
        if (target_id is None) != (base_revision is None):
            raise ApiError(400, "VALIDATION_ERROR", "an edited update needs both target_id and base_revision")
    elif decision == "update":
        target_id = body.get("target_id")
        base_revision = body.get("base_revision")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ApiError(400, "VALIDATION_ERROR", "target_id is required")
        if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1:
            raise ApiError(400, "VALIDATION_ERROR", "base_revision is required")
        if isinstance(body.get("content"), str) and body.get("content").strip():
            content = body.get("content")
        kind = body.get("kind") or kind
        category = body.get("category") or category
        if body.get("scope"):
            scope = body.get("scope")
    elif decision == "keep_both":
        scope = body.get("scope")
        if not isinstance(scope, str) or not scope.strip():
            raise ApiError(400, "VALIDATION_ERROR", "keep both requires an explicit scope")
        if isinstance(body.get("content"), str) and body.get("content").strip():
            content = body.get("content")
        kind = body.get("kind") or kind
        category = body.get("category") or category
    active = None
    if target_id:
        active = connection.execute(
            """
            SELECT revision, share_enabled, valid_until FROM memory_refs
            WHERE memory_id = ? AND lifecycle = 'active'
            """,
            (target_id,),
        ).fetchone()
        if active is None:
            raise ApiError(404, "NOT_FOUND", "memory not found")
        if active["revision"] != base_revision:
            raise ApiError(409, "CONFLICT", VERSION_CONFLICT)
    # An update, or an edit aimed at an existing memory, keeps the current
    # share flag and expiry unless this decision sets them. A missing
    # candidate value must not turn sharing on. A new memory still defaults
    # to shared and takes its expiry from the candidate.
    if decision == "update" or (decision == "edit" and target_id):
        share_enabled = bool(active["share_enabled"])
        valid_until = active["valid_until"]
        if "share_enabled" in body:
            share_enabled = _required_bool(body.get("share_enabled"))
        if "valid_until" in body:
            valid_until = _expiry(body.get("valid_until"))
    else:
        share_enabled = True if stored.get("share_enabled") is None else bool(stored.get("share_enabled"))
        if "share_enabled" in body:
            share_enabled = _required_bool(body.get("share_enabled"))
        valid_until = stored.get("valid_until")
    return {
        "content": content,
        "kind": kind,
        "category": category,
        "scope": scope,
        "valid_until": valid_until,
        "share_enabled": share_enabled,
        "source_refs": [proposal["source_id"]],
        "target_id": target_id,
        "base_revision": base_revision,
        "approved_evidence": proposal["evidence_json"],
    }


def _accept(connection: sqlite3.Connection, proposal_id: str, decision: str, operation_id: str) -> None:
    cursor = connection.execute(
        """
        UPDATE proposals
        SET status = 'accepted', decision = ?, operation_id = ?
        WHERE id = ? AND status IN ('publishing', 'failed')
        """,
        (decision, operation_id, proposal_id),
    )
    if cursor.rowcount != 1:
        current = connection.execute("SELECT status FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if current is None or current["status"] != "accepted":
            raise ApiError(409, "CONFLICT", "the proposal was no longer waiting to publish")


def _reopen(connection: sqlite3.Connection, proposal_id: str, operation_id: str, *, fail_operation: bool) -> None:
    published = connection.execute(
        "SELECT COUNT(*) FROM memory_refs WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()[0]
    if published:
        return
    if fail_operation:
        connection.execute(
            """
            UPDATE operations
            SET status = 'failed', error_code = 'CONFLICT', updated_at = datetime('now')
            WHERE id = ? AND status = 'prepared'
            """,
            (operation_id,),
        )
    connection.execute(
        """
        UPDATE proposals
        SET status = 'pending', operation_id = NULL, decision = NULL
        WHERE id = ? AND status = 'publishing'
        """,
        (proposal_id,),
    )
    connection.commit()


def _intent(body: dict) -> dict:
    decision = body.get("decision")
    control = _control_intent(body)
    if decision == "reject":
        return {
            "base_revision": None,
            "category": None,
            "content": None,
            "decision": "reject",
            "kind": None,
            "scope": None,
            "target_id": None,
            **control,
        }
    return {
        "base_revision": _revision(body.get("base_revision")),
        "category": _label(body.get("category")),
        "content": _text(body.get("content")),
        "decision": decision,
        "kind": _label(body.get("kind")),
        "scope": _text(body.get("scope")),
        "target_id": _text(body.get("target_id")),
        **control,
    }


def _control_intent(body: dict) -> dict:
    """Record the submitted share flag and expiry, including whether each was sent.

    A missing key is not the same intent as an explicit null. The resolved
    inheritance is stored on the operation, not copied into this intent.
    """
    share_provided = "share_enabled" in body
    until_provided = "valid_until" in body
    share_value = body.get("share_enabled") if share_provided else None
    if not isinstance(share_value, bool):
        share_value = None
    until_value = None
    if until_provided and isinstance(body.get("valid_until"), str) and body.get("valid_until").strip():
        until_value = body.get("valid_until").strip()
    return {
        "share_enabled": share_value,
        "share_provided": share_provided,
        "valid_until": until_value,
        "valid_until_provided": until_provided,
    }


def _same_intent(stored: dict | None, incoming: dict) -> bool:
    if not isinstance(stored, dict):
        return False
    left = dict(stored)
    left.setdefault("share_enabled", None)
    left.setdefault("share_provided", False)
    left.setdefault("valid_until", None)
    left.setdefault("valid_until_provided", False)
    return left == incoming


def _stored_intent(proposal) -> dict | None:
    raw = proposal["intent_json"]
    if not raw:
        return None
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else None


def _replay_message(proposal) -> str:
    label = _DECISION_LABEL.get(proposal["decision"] or "", "未知")
    if proposal["status"] == "accepted":
        state = "已经通过"
    elif proposal["status"] == "rejected":
        state = "已经拒绝"
    elif proposal["status"] == "publishing":
        state = "正在保存"
    elif proposal["status"] == "failed":
        state = "上次保存失败"
    else:
        state = proposal["status"]
    return f"这条待确认{state}，已保存的决定是「{label}」。这次提交不同，正式记忆没有改动。"


def _dump(intent: dict) -> str:
    return json.dumps(intent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required_bool(value) -> bool:
    if not isinstance(value, bool):
        raise ApiError(400, "VALIDATION_ERROR", "share_enabled must be a boolean")
    return value


def _expiry(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "VALIDATION_ERROR", "valid_until is invalid")
    return value.strip()


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _label(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return value


def _decision_view(proposal, published: dict | None) -> dict:
    if published is None:
        return {
            "proposal_id": proposal["id"],
            "status": "rejected",
            "decision": proposal["decision"] or "reject",
        }
    view = dict(published)
    view["proposal_id"] = proposal["id"]
    view["decision"] = proposal["decision"]
    view["status"] = "accepted"
    return view


def _proposal_view(row, *, demo: bool = False, agents: dict | None = None, clients: dict | None = None) -> dict:
    directory_agents = agents or {}
    directory_clients = clients or {}
    return {
        "id": row["id"],
        "origin": row["origin"],
        "requester": requester_of(row["agent_id"], directory_agents, directory_clients),
        "change_type": row["change_type"],
        "target_id": row["target_id"],
        "base_revision": row["base_revision"],
        "status": row["status"],
        "decision": row["decision"],
        "operation_id": row["operation_id"],
        "created_at": row["created_at"],
        "batch_id": json.loads(row["payload_json"]).get("batch_id") or row["job_id"],
        "payload": json.loads(row["payload_json"]),
        "evidence": json.loads(row["evidence_json"]),
        "source": {
            "id": row["source_id"],
            "kind": row["source_kind"],
            "name": row["source_name"],
        },
        "demo": demo,
    }
