"""Owner-managed agent connections.

Identity is the credential hash. This module does not read or write
memories. policy_version increases when tools, read or proposal
categories, enabled, or the credential change. A name-only edit does not.

Read categories (allowed_categories) decide what the connection may see.
Proposal categories (propose_categories) decide what it may suggest for
review. Granting one never grants the other.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Request

from zhiwo.api.auth import bearer_token, credential_digest, install_redaction
from zhiwo.api.errors import ApiError
from zhiwo.contracts.memory import CATEGORIES, TOOLS
from zhiwo.services.commit_gate import commit_lock

_lock = commit_lock


@dataclass(frozen=True)
class AgentPrincipal:
    agent_id: str
    name: str
    enabled: bool
    policy_version: int
    allowed_tools: tuple[str, ...]
    allowed_categories: tuple[str, ...]
    propose_categories: tuple[str, ...] = ()


def require_agent(request: Request) -> AgentPrincipal:
    """Authenticate an agent request. Does not authorize a tool or a memory."""
    principal = resolve_agent(request.app.state.settings.control_db, bearer_token(request.headers.get("authorization")))
    if principal is None:
        raise ApiError(401, "UNAUTHENTICATED", "agent credential rejected")
    if not principal.enabled:
        raise ApiError(403, "FORBIDDEN", "agent is disabled")
    return principal


def resolve_agent(db_path, credential: str) -> AgentPrincipal | None:
    """Map a presented credential to the stored connection.

    A disabled connection is still returned. Callers decide whether a
    disabled principal may continue. Unknown credentials return None.
    The lookup never uses a client-supplied name or agent id.
    """
    connection = _connect(db_path)
    try:
        return fetch_principal(connection, credential)
    finally:
        connection.close()


def fetch_principal_by_id(connection, agent_id: str) -> AgentPrincipal | None:
    """Read one connection by its id, including a disabled row."""
    if not isinstance(agent_id, str) or not agent_id:
        return None
    row = connection.execute(
        """
        SELECT agents.id, agents.name, agents.enabled, agents.policy_version,
               agent_permissions.allowed_tools, agent_permissions.allowed_categories,
               agent_permissions.propose_categories
        FROM agents
        LEFT JOIN agent_permissions ON agent_permissions.agent_id = agents.id
        WHERE agents.id = ?
        """,
        (agent_id,),
    ).fetchone()
    return _principal_from_row(row)


def note_client_observed(db_path, agent_id: str) -> None:
    """Record that a real stdio call was handed to the client.

    Only an enabled connection waiting for verification changes. A disabled
    connection stays disabled and is not marked connected.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE agents
                SET client_status = 'verified', updated_at = ?
                WHERE id = ? AND enabled = 1 AND client_status = 'pending'
                """,
                (_now(), agent_id),
            )
            connection.commit()
        finally:
            connection.close()


def fetch_principal(connection, credential: str) -> AgentPrincipal | None:
    """Read identity and permissions in one statement.

    The caller can run this inside the commit lock's read transaction so
    the enabled flag, policy version, tools, and categories match.
    """
    if not isinstance(credential, str) or not credential:
        return None
    row = connection.execute(
        """
        SELECT agents.id, agents.name, agents.enabled, agents.policy_version,
               agent_permissions.allowed_tools, agent_permissions.allowed_categories,
               agent_permissions.propose_categories
        FROM agents
        LEFT JOIN agent_permissions ON agent_permissions.agent_id = agents.id
        WHERE agents.credential_hash = ?
        """,
        (credential_digest(credential),),
    ).fetchone()
    return _principal_from_row(row)


def _principal_from_row(row) -> AgentPrincipal | None:
    if row is None:
        return None
    if row["allowed_tools"] is None or row["allowed_categories"] is None:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "agent permissions are missing", retryable=True)
    permissions = {
        "allowed_tools": _load_list(row["allowed_tools"]),
        "allowed_categories": _load_list(row["allowed_categories"]),
        "propose_categories": _load_list(row["propose_categories"] or "[]"),
    }
    return AgentPrincipal(
        agent_id=row["id"],
        name=row["name"],
        enabled=bool(row["enabled"]),
        policy_version=int(row["policy_version"]),
        allowed_tools=tuple(permissions["allowed_tools"]),
        allowed_categories=tuple(permissions["allowed_categories"]),
        propose_categories=tuple(permissions["propose_categories"]),
    )


def create_agent(db_path, request_id: str, name: str, *, secret: str | None = None) -> dict:
    cleaned = _name(name)
    digest = _payload_hash({"name": cleaned})
    with _lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = _prior(connection, request_id, "create", digest)
            if prior is not None:
                view = _public(connection, prior["agent_id"])
                connection.rollback()
                return view
            agent_id = str(uuid.uuid4())
            if secret is None:
                secret = secrets.token_urlsafe(32)
            elif not isinstance(secret, str) or not secret:
                connection.rollback()
                raise ApiError(500, "UNAVAILABLE", "这次没有生成可用凭证，配置没有写入。")
            install_redaction(secret)
            now = _now()
            connection.execute(
                """
                INSERT INTO agents (
                    id, name, credential_hash, enabled, policy_version, created_at, updated_at
                ) VALUES (?, ?, ?, 1, 1, ?, ?)
                """,
                (agent_id, cleaned, credential_digest(secret), now, now),
            )
            connection.execute(
                """
                INSERT INTO agent_permissions (agent_id, allowed_tools, allowed_categories, propose_categories)
                VALUES (?, '[]', '[]', '[]')
                """,
                (agent_id,),
            )
            _remember(connection, request_id, agent_id, "create", digest, now)
            connection.commit()
            view = _public(connection, agent_id)
            view["credential"] = secret
            return view
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def list_agents(db_path) -> dict:
    _rename_chatgpt(db_path)
    connection = _connect(db_path)
    try:
        rows = connection.execute("SELECT id FROM agents ORDER BY created_at, id").fetchall()
        return {"agents": [_public(connection, row["id"]) for row in rows]}
    finally:
        connection.close()


def update_agent(db_path, agent_id: str, request_id: str, patch: dict) -> dict:
    name = patch.get("name")
    enabled = patch.get("enabled")
    tools = patch.get("allowed_tools")
    categories = patch.get("allowed_categories")
    propose = patch.get("propose_categories")
    if name is not None:
        name = _name(name)
    if enabled is not None and not isinstance(enabled, bool):
        raise ApiError(400, "VALIDATION_ERROR", "enabled must be a boolean")
    if tools is not None:
        tools = _choices(tools, TOOLS, "tool")
    if categories is not None:
        categories = _choices(categories, CATEGORIES, "category")
    if propose is not None:
        propose = _choices(propose, CATEGORIES, "category")
    hashed = {
        "allowed_categories": categories,
        "allowed_tools": tools,
        "enabled": enabled,
        "name": name,
    }
    if propose is not None:
        hashed["propose_categories"] = propose
    digest = _payload_hash(hashed)
    with _lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = _prior(connection, request_id, "update", digest)
            if prior is not None:
                if prior["agent_id"] != agent_id:
                    connection.rollback()
                    raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
                view = _public(connection, agent_id)
                connection.rollback()
                return view
            current = _required(connection, agent_id)
            permissions = _permissions(connection, agent_id)
            next_name = current["name"] if name is None else name
            next_enabled = bool(current["enabled"]) if enabled is None else enabled
            next_tools = permissions["allowed_tools"] if tools is None else tools
            next_categories = permissions["allowed_categories"] if categories is None else categories
            next_propose = permissions["propose_categories"] if propose is None else propose
            newly_proposing = "propose_memory" in next_tools and "propose_memory" not in permissions["allowed_tools"]
            if propose is None and newly_proposing and not next_propose:
                # Turning on proposals without naming categories means every
                # category, as the one-click preset does. Reads stay as set.
                next_propose = list(CATEGORIES)
            policy_changed = (
                next_enabled != bool(current["enabled"])
                or next_tools != permissions["allowed_tools"]
                or next_categories != permissions["allowed_categories"]
                or next_propose != permissions["propose_categories"]
            )
            policy_version = int(current["policy_version"]) + (1 if policy_changed else 0)
            now = _now()
            connection.execute(
                """
                UPDATE agents
                SET name = ?, enabled = ?, policy_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_name, 1 if next_enabled else 0, policy_version, now, agent_id),
            )
            connection.execute(
                """
                UPDATE agent_permissions
                SET allowed_tools = ?, allowed_categories = ?, propose_categories = ?
                WHERE agent_id = ?
                """,
                (_dump(next_tools), _dump(next_categories), _dump(next_propose), agent_id),
            )
            _remember(connection, request_id, agent_id, "update", digest, now)
            connection.commit()
            return _public(connection, agent_id)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def rotate_credential(db_path, agent_id: str, request_id: str) -> dict:
    digest = _payload_hash({"action": "rotate", "agent_id": agent_id})
    with _lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _required(connection, agent_id)
            prior = _prior(connection, request_id, "rotate", digest)
            if prior is not None:
                if prior["agent_id"] != agent_id:
                    connection.rollback()
                    raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
                view = _public(connection, agent_id)
                connection.rollback()
                return view
            secret = secrets.token_urlsafe(32)
            install_redaction(secret)
            now = _now()
            connection.execute(
                """
                UPDATE agents
                SET credential_hash = ?, policy_version = policy_version + 1, updated_at = ?
                WHERE id = ?
                """,
                (credential_digest(secret), now, agent_id),
            )
            _remember(connection, request_id, agent_id, "rotate", digest, now)
            connection.commit()
            view = _public(connection, agent_id)
            view["credential"] = secret
            return view
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def commit_credential(db_path, agent_id: str, secret: str) -> None:
    """Store a credential that is already in the client config.

    The caller writes the config first. A failed write must not call this,
    so the previous credential remains valid.
    """
    if not isinstance(secret, str) or not secret:
        raise ApiError(500, "UNAVAILABLE", "这次没有生成可用凭证，配置没有写入。")
    install_redaction(secret)
    with _lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _required(connection, agent_id)
            now = _now()
            connection.execute(
                """
                UPDATE agents
                SET credential_hash = ?, policy_version = policy_version + 1, updated_at = ?
                WHERE id = ?
                """,
                (credential_digest(secret), now, agent_id),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()


def _rename_chatgpt(db_path) -> None:
    """The built-in row is ChatGPT. Older connects stored the name Codex."""
    connection = _connect(db_path)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", ("client_agent:codex",)).fetchone()
        if row is None:
            return
        updated = connection.execute(
            "UPDATE agents SET name = ? WHERE id = ? AND name = ?",
            ("ChatGPT", row["value"], "Codex"),
        )
        if updated.rowcount:
            connection.commit()
    finally:
        connection.close()


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _prior(connection, request_id: str, action: str, digest: str):
    row = connection.execute("SELECT * FROM agent_commands WHERE id = ?", (request_id,)).fetchone()
    if row is None:
        return None
    if row["action"] != action or row["payload_hash"] != digest:
        raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
    return row


def _required(connection, agent_id: str):
    row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "agent not found")
    return row


def _remember(connection, request_id: str, agent_id: str, action: str, digest: str, now: str) -> None:
    connection.execute(
        """
        INSERT INTO agent_commands (id, agent_id, action, payload_hash, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (request_id, agent_id, action, digest, now),
    )


def _public(connection, agent_id: str) -> dict:
    row = _required(connection, agent_id)
    permissions = _permissions(connection, agent_id)
    last = connection.execute(
        "SELECT MAX(created_at) FROM access_events WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "policy_version": int(row["policy_version"]),
        "allowed_tools": permissions["allowed_tools"],
        "allowed_categories": permissions["allowed_categories"],
        "propose_categories": permissions["propose_categories"],
        "client_status": row["client_status"] if row["client_status"] in {"pending", "verified"} else "pending",
        "last_access_at": last[0] if last else None,
    }


def _permissions(connection, agent_id: str) -> dict:
    row = connection.execute(
        "SELECT allowed_tools, allowed_categories, propose_categories FROM agent_permissions WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    if row is None:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "agent permissions are missing", retryable=True)
    return {
        "allowed_tools": _load_list(row["allowed_tools"]),
        "allowed_categories": _load_list(row["allowed_categories"]),
        "propose_categories": _load_list(row["propose_categories"] or "[]"),
    }


def _choices(value, allowed: tuple[str, ...], label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ApiError(400, "VALIDATION_ERROR", f"{label} list is invalid")
    if len(value) != len(set(value)):
        raise ApiError(400, "VALIDATION_ERROR", f"duplicate {label}")
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", f"{label} is not supported")
    return list(value)


def _name(value) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "name is required")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 200:
        raise ApiError(400, "VALIDATION_ERROR", "name must contain 1 to 200 characters")
    return cleaned


def _load_list(raw: str) -> list[str]:
    loaded = json.loads(raw)
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


def _dump(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _payload_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
