"""Versioned migrations for the control database.

Official memory text is not stored here. Repeat runs skip applied versions.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FROZEN_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
FROZEN_KERNEL = "mnemosyne-memory==3.15.1"

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('schema_version', '1'),
    ('embedding_model', 'BAAI/bge-small-zh-v1.5'),
    ('kernel_package', 'mnemosyne-memory==3.15.1');

CREATE TABLE IF NOT EXISTS memory_refs (
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    kernel_id TEXT,
    kernel_session TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'event')),
    category TEXT NOT NULL CHECK (
        category IN ('identity', 'goal', 'preference', 'project', 'event', 'other')
    ),
    scope TEXT,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'superseded', 'deleting')),
    share_enabled INTEGER NOT NULL DEFAULT 0 CHECK (share_enabled IN (0, 1)),
    valid_until TEXT,
    source_refs TEXT,
    approved_evidence TEXT,
    operation_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, revision),
    UNIQUE (kernel_session)
);
"""

_MIGRATION_2 = """
CREATE TABLE memory_refs_v2 (
    memory_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    kernel_id TEXT,
    kernel_session TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'event')),
    category TEXT NOT NULL CHECK (
        category IN ('identity', 'goal', 'preference', 'project', 'event', 'other')
    ),
    scope TEXT,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('active', 'superseded', 'deleting')),
    share_enabled INTEGER NOT NULL DEFAULT 0 CHECK (share_enabled IN (0, 1)),
    valid_until TEXT,
    source_refs TEXT,
    approved_evidence TEXT,
    operation_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, revision),
    UNIQUE (kernel_session),
    CHECK (lifecycle != 'active' OR (kernel_id IS NOT NULL AND length(kernel_id) > 0))
);

INSERT INTO memory_refs_v2 (
    memory_id, revision, kernel_id, kernel_session, kind, category, scope,
    lifecycle, share_enabled, valid_until, source_refs, approved_evidence,
    operation_id, created_at
)
SELECT
    memory_id, revision, kernel_id, kernel_session, kind, category, scope,
    lifecycle, share_enabled, valid_until, source_refs, approved_evidence,
    operation_id, created_at
FROM memory_refs;

DROP TABLE memory_refs;
ALTER TABLE memory_refs_v2 RENAME TO memory_refs;
CREATE UNIQUE INDEX memory_refs_one_active
    ON memory_refs(memory_id) WHERE lifecycle = 'active';

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('manual', 'paste', 'file', 'agent_claim')),
    name TEXT,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id),
    status TEXT NOT NULL CHECK (status IN ('extracted', 'extractor_unavailable', 'failed')),
    extractor_config TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    origin TEXT NOT NULL CHECK (origin IN ('import', 'agent')),
    change_type TEXT NOT NULL CHECK (change_type IN ('add', 'update')),
    target_id TEXT,
    base_revision INTEGER,
    payload_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'publishing', 'accepted', 'rejected', 'failed')),
    decision TEXT,
    operation_id TEXT,
    source_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target_id TEXT,
    base_revision INTEGER,
    payload_hash TEXT NOT NULL,
    kernel_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'completed', 'failed')),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

UPDATE settings SET value = '2' WHERE key = 'schema_version';
"""

def _apply_agent_identity(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            credential_hash TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
            policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_permissions (
            agent_id TEXT PRIMARY KEY REFERENCES agents(id),
            allowed_tools TEXT NOT NULL,
            allowed_categories TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_commands (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            action TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.execute("UPDATE settings SET value = '5' WHERE key = 'schema_version'")


def _apply_review_intent(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(proposals)")}
    if "intent_json" not in columns:
        connection.execute("ALTER TABLE proposals ADD COLUMN intent_json TEXT")
    connection.execute("UPDATE settings SET value = '4' WHERE key = 'schema_version'")


def _apply_operation_payload(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(operations)")}
    if "payload_json" not in columns:
        connection.execute("ALTER TABLE operations ADD COLUMN payload_json TEXT")
    if "revision" not in columns:
        connection.execute("ALTER TABLE operations ADD COLUMN revision INTEGER")
    connection.execute("UPDATE settings SET value = '3' WHERE key = 'schema_version'")


def _apply_access_ledger(connection: sqlite3.Connection) -> None:
    proposal_columns = {row[1] for row in connection.execute("PRAGMA table_info(proposals)")}
    if "agent_id" not in proposal_columns:
        connection.execute("ALTER TABLE proposals ADD COLUMN agent_id TEXT")
    if "client_request_id" not in proposal_columns:
        connection.execute("ALTER TABLE proposals ADD COLUMN client_request_id TEXT")
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS proposals_agent_request
        ON proposals(agent_id, client_request_id)
        WHERE agent_id IS NOT NULL AND client_request_id IS NOT NULL
        """
    )
    agent_columns = {row[1] for row in connection.execute("PRAGMA table_info(agents)")}
    if "client_status" not in agent_columns:
        connection.execute("ALTER TABLE agents ADD COLUMN client_status TEXT NOT NULL DEFAULT 'pending'")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS access_events (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            agent_id TEXT,
            tool TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('success', 'empty', 'rejected')),
            policy_version INTEGER,
            response_snapshot TEXT NOT NULL,
            created_at TEXT NOT NULL,
            delivery_state TEXT NOT NULL CHECK (
                delivery_state IN ('prepared', 'sent', 'failed', 'unknown')
            )
        );

        CREATE INDEX IF NOT EXISTS access_events_agent
        ON access_events(agent_id, created_at);
        """
    )
    connection.execute("UPDATE settings SET value = '6' WHERE key = 'schema_version'")


def _apply_profile_summary(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_summary (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            text TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            model TEXT NOT NULL,
            memory_count INTEGER NOT NULL CHECK (memory_count >= 0)
        )
        """
    )
    connection.execute("UPDATE settings SET value = '7' WHERE key = 'schema_version'")


MIGRATIONS: tuple[tuple[int, str, object], ...] = (
    (1, "control_identity", _MIGRATION_1),
    (2, "sources_and_publish", _MIGRATION_2),
    (3, "operation_payload", _apply_operation_payload),
    (4, "review_intent", _apply_review_intent),
    (5, "agent_identity", _apply_agent_identity),
    (6, "access_ledger", _apply_access_ledger),
    (7, "profile_summary", _apply_profile_summary),
)


def migrate(db_path: Path) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
        for version, name, script in MIGRATIONS:
            if version in applied:
                continue
            if callable(script):
                script(connection)
            else:
                connection.executescript(script)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (version, name, _now()),
            )
        connection.commit()
        current = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        return int(current or 0)
    finally:
        connection.close()


def schema_version(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return int(row[0] or 0)
    finally:
        connection.close()


def setting(db_path: Path, key: str) -> str | None:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        connection.close()


def put_setting(db_path: Path, key: str, value: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()
    finally:
        connection.close()


def drop_setting(db_path: Path, key: str) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DELETE FROM settings WHERE key = ?", (key,))
        connection.commit()
    finally:
        connection.close()


def memory_ref_count(db_path: Path) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0])
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
