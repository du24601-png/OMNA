"""Read-only search over rows already published into the Kernel.

P0 experiment mapping only. It is not the product Gateway and it is not
the P1/P2 read path. There is no publish-state check and no permission
check here. `kind="unspecified"` and using scenario as category are
experiment labels. Comparing control text to the kernel row is an
experiment assertion, not the way a product read identifies a memory.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from zhiwo.adapters.kernel_session import kernel_session

MAX_ITEM_CHARS = 2000
MAX_TOTAL_CHARS = 8000
MAX_RESPONSE_LIMIT = 20
# Response `limit` is applied after qualification. Recall must see past
# unqualified hits, so this window stays above the maximum response size.
# It is an experiment bound, not a product-wide scan and not a permission filter.
RECALL_CANDIDATES = MAX_RESPONSE_LIMIT * 2


class SearchRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def load_catalog(db_path: Path, control_path: Path) -> dict[str, dict[str, Any]]:
    """Map a kernel id to its control record.

    Rows that are not in the control file, or whose session does not match
    zhiwo:{memory_id}:r{revision}, are omitted and cannot be returned.
    """
    control = json.loads(control_path.read_text(encoding="utf-8"))
    by_session = {row["session"]: row for row in control}
    connection = sqlite3.connect(f"file:///{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT id, session_id FROM working_memory").fetchall()
    finally:
        connection.close()
    catalog: dict[str, dict[str, Any]] = {}
    for kernel_id, session_id in rows:
        record = by_session.get(session_id)
        if record is None:
            continue
        expected = kernel_session(str(record["memory_id"]), int(record["revision"]))
        if session_id != expected:
            continue
        catalog[str(kernel_id)] = record
    return catalog


def check_search_arguments(
    query: str,
    limit: int = 10,
    categories: list[str] | None = None,
) -> tuple[str, int, set[str] | None]:
    if not isinstance(query, str) or not query.strip():
        raise SearchRejected("VALIDATION_ERROR", "query is required")
    if len(query) > MAX_ITEM_CHARS:
        raise SearchRejected("VALIDATION_ERROR", "query is too long")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESPONSE_LIMIT:
        raise SearchRejected("VALIDATION_ERROR", "limit must be an integer from 1 to 20")
    if categories is None:
        allowed = None
    elif (
        isinstance(categories, list)
        and all(isinstance(item, str) and item.strip() for item in categories)
    ):
        allowed = set(categories)
    else:
        raise SearchRejected("VALIDATION_ERROR", "categories must be a list of strings")
    return query.strip(), limit, allowed


def search_published(
    recall_rows: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    query: str,
    limit: int = 10,
    categories: list[str] | None = None,
) -> dict[str, Any]:
    """Turn one Kernel recall into MemoryItem rows.

    `limit` counts rows that pass the experiment checks. The caller must
    pass the recall window uncut; this function does not slice first.
    A further qualified row beyond `limit`, or a row that does not fit
    the character budget, sets `truncated`.
    """
    query, limit, allowed = check_search_arguments(query, limit, categories)

    items: list[dict[str, Any]] = []
    total = 0
    truncated = False
    for row in recall_rows:
        kernel_id = str(row.get("id") or "")
        record = catalog.get(kernel_id)
        kernel_content = str(row.get("content") or "")
        # Experiment assertion only. Product identity is the published
        # revision, not a string compare against a control copy.
        if record is None or kernel_content != record["content"]:
            continue
        scenario = str(record["scenario"])
        if allowed is not None and scenario not in allowed:
            continue
        if len(items) >= limit:
            truncated = True
            break
        content = kernel_content
        if len(content) > MAX_ITEM_CHARS or total + len(content) > MAX_TOTAL_CHARS:
            truncated = True
            break
        total += len(content)
        items.append(
            {
                "id": record["memory_id"],
                "revision": int(record["revision"]),
                "content": content,
                # Experiment labels. Product kind and category come from
                # the reviewed control record. Scenario is not a category.
                "kind": "unspecified",
                "category": scenario,
                "scope": scenario,
                "valid_until": record["valid_until"],
            }
        )
    return {"items": items, "truncated": truncated}
