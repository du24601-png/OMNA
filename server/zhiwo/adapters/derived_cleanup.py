"""Delete derived rows that belong to one kernel id.

Approved exception for mnemosyne-memory 3.15.1 only. This is not a general
database repair tool.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

REQUIRED_VERSION = "3.15.1"
GIST_ID_COLUMN = "memory_id"
FACT_ID_COLUMN = "source_memory_id"
GISTS_REQUIRED = {"id", "text", "memory_id"}
FACTS_REQUIRED = {"id", "source_memory_id", "context_snippet", "key", "value"}


class DerivedCleanupError(RuntimeError):
    """The approved cleanup cannot run safely."""


class SharedDerivedRowError(DerivedCleanupError):
    """A derived row is not owned by exactly one kernel id."""


def cleanup_derived_rows(db_path: Path, kernel_id: str) -> dict[str, int]:
    """Remove gists and memoria_facts rows owned by kernel_id.

    Safe to retry after the working-memory body is already gone.
    Both deletes commit together or not at all.
    """
    if not isinstance(kernel_id, str) or not kernel_id.strip():
        raise DerivedCleanupError("kernel_id is required")
    import mnemosyne

    if getattr(mnemosyne, "__version__", None) != REQUIRED_VERSION:
        raise DerivedCleanupError(
            f"cleanup requires mnemosyne-memory {REQUIRED_VERSION}, "
            f"found {getattr(mnemosyne, '__version__', None)}"
        )

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        _require_columns(connection, "gists", GISTS_REQUIRED)
        _require_columns(connection, "memoria_facts", FACTS_REQUIRED)
        _refuse_shared(connection, "gists", "id", GIST_ID_COLUMN, kernel_id)
        _refuse_shared(connection, "memoria_facts", "id", FACT_ID_COLUMN, kernel_id)
        gists = connection.execute(
            f"DELETE FROM gists WHERE {GIST_ID_COLUMN} = ?",
            (kernel_id,),
        ).rowcount
        facts = connection.execute(
            f"DELETE FROM memoria_facts WHERE {FACT_ID_COLUMN} = ?",
            (kernel_id,),
        ).rowcount
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"gists": gists, "memoria_facts": facts}


def _require_columns(connection: sqlite3.Connection, table: str, required: set[str]) -> None:
    found = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if not found:
        raise DerivedCleanupError(f"missing table {table}")
    missing = required - found
    if missing:
        raise DerivedCleanupError(f"{table} missing columns: {sorted(missing)}")


def _refuse_shared(
    connection: sqlite3.Connection,
    table: str,
    row_id: str,
    owner_column: str,
    kernel_id: str,
) -> None:
    rows = connection.execute(
        f"SELECT {row_id} AS row_id, {owner_column} AS owner FROM {table} WHERE {owner_column} = ?",
        (kernel_id,),
    ).fetchall()
    for row in rows:
        if row["owner"] != kernel_id:
            raise SharedDerivedRowError(f"{table} row is not owned by {kernel_id}")
        others = connection.execute(
            f"SELECT {owner_column} AS owner FROM {table} WHERE {row_id} = ? AND {owner_column} != ?",
            (row["row_id"], kernel_id),
        ).fetchall()
        if others:
            raise SharedDerivedRowError(
                f"{table} row {row['row_id']} is also owned by another memory"
            )
