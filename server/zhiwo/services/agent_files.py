"""User-level instruction files that coding agents read, on a fixed whitelist.

Listing only looks at size and time; it never opens a file. Reading happens
once, when the owner asks to import a file by its id, and is read-only: the
file is opened for reading and its timestamps are not touched. A path that
leaves the user directory through a link is refused. Paths were checked
against each client's documentation on 2026-10-02.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zhiwo.api.errors import ApiError
from zhiwo.services.client_connect import client_home

MAX_BYTES = 1024 * 1024


@dataclass(frozen=True)
class AgentFile:
    id: str
    clients: tuple[tuple[str, str], ...]
    parts: tuple[str, ...]

    @property
    def display(self) -> str:
        return "~/" + "/".join(self.parts)


# Claude Code reads ~/.claude/CLAUDE.md; OpenCode falls back to the same file,
# so it is listed once. Codex reads AGENTS.override.md when present.
WHITELIST: tuple[AgentFile, ...] = (
    AgentFile("claude-code", (("claude-code", "Claude Code"), ("opencode", "OpenCode")), (".claude", "CLAUDE.md")),
    AgentFile("codex-override", (("codex", "ChatGPT"),), (".codex", "AGENTS.override.md")),
    AgentFile("codex", (("codex", "ChatGPT"),), (".codex", "AGENTS.md")),
    AgentFile("opencode", (("opencode", "OpenCode"),), (".config", "opencode", "AGENTS.md")),
    AgentFile("zcode", (("zcode", "ZCode"),), (".zcode", "AGENTS.md")),
)
_BY_ID = {item.id: item for item in WHITELIST}


def list_agent_files(db_path) -> dict:
    home = client_home()
    imported = _imported(db_path)
    files = []
    for item in WHITELIST:
        path = home.joinpath(*item.parts)
        info = _stat(home, path)
        if info is None:
            continue
        size, modified = info
        files.append(
            {
                "id": item.id,
                "clients": [{"id": client_id, "name": name} for client_id, name in item.clients],
                "path": item.display,
                "size": size,
                "modified_at": modified,
                "empty": size == 0,
                "imported_at": imported.get(item.display),
            }
        )
    return {"files": files}


def read_agent_file(file_id: object) -> tuple[str, str]:
    """Return (display path, text) for one whitelisted file. Read-only."""
    item = _BY_ID.get(file_id) if isinstance(file_id, str) else None
    if item is None:
        raise ApiError(400, "VALIDATION_ERROR", "这个说明文件不在可导入的名单里。")
    home = client_home()
    path = home.joinpath(*item.parts)
    info = _stat(home, path)
    if info is None:
        raise ApiError(404, "NOT_FOUND", "没有找到这个说明文件。")
    if info[0] > MAX_BYTES:
        raise ApiError(400, "VALIDATION_ERROR", "a source can contain at most 1 MiB")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ApiError(400, "VALIDATION_ERROR", "a source can contain at most 1 MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "file text must be valid UTF-8") from exc
    if not text.strip():
        raise ApiError(400, "VALIDATION_ERROR", "这个说明文件是空的。")
    return item.display, text


def _stat(home: Path, path: Path) -> tuple[int, str] | None:
    try:
        if not path.is_file():
            return None
        if not path.resolve().is_relative_to(home.resolve()):
            return None
        stat = path.stat()
    except OSError:
        return None
    modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    return stat.st_size, modified


def _imported(db_path) -> dict[str, str]:
    names = [item.display for item in WHITELIST]
    connection = sqlite3.connect(db_path)
    try:
        marks = ",".join("?" for _ in names)
        rows = connection.execute(
            f"SELECT name, MAX(imported_at) FROM sources WHERE kind = 'file' AND name IN ({marks}) GROUP BY name",
            names,
        ).fetchall()
    finally:
        connection.close()
    return {row[0]: row[1] for row in rows}
