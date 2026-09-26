"""Write one local stdio bridge entry into the known clients.

The bridge and the four tools stay unchanged. This module only detects a
fixed install and merges a single `zhiwo` server into that client's own
config. It never replaces the rest of the file, and it never returns the
credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import put_setting, setting
from zhiwo.services.agents import create_agent, rotate_credential, update_agent
from zhiwo.services.commit_gate import commit_lock

READ_CATEGORIES = ("preference", "goal")
READ_TOOLS = ("get_context", "search_memory")
PROPOSE_TOOLS = (*READ_TOOLS, "propose_memory")
_PRESETS = {
    "read": {"allowed_categories": list(READ_CATEGORIES), "allowed_tools": list(READ_TOOLS)},
    "propose": {"allowed_categories": list(READ_CATEGORIES), "allowed_tools": list(PROPOSE_TOOLS)},
}


@dataclass(frozen=True)
class ClientProfile:
    id: str
    name: str
    command_name: str
    markers: tuple[str, ...]


PROFILES = (
    ClientProfile("workbuddy", "WorkBuddy", "workbuddy", (".workbuddy",)),
    ClientProfile("zcode", "ZCode", "zcode", (".zcode",)),
    ClientProfile("opencode", "OpenCode", "opencode", (".config/opencode",)),
    ClientProfile("codex", "ChatGPT", "codex", (".codex",)),
    ClientProfile("claude", "Claude", "claude-desktop", ("AppData/Roaming/Claude",)),
    ClientProfile("claude-code", "Claude Code", "claude", (".claude", ".claude.json")),
)
_BY_ID = {profile.id: profile for profile in PROFILES}


def client_home() -> Path:
    """User home. Tests may redirect it only while test mode is on."""
    if os.environ.get("ZHIWO_TEST_MODE") == "1":
        override = os.environ.get("ZHIWO_CLIENT_HOME")
        if override:
            return Path(override)
    return Path.home()


def bridge_launch(port: int) -> dict:
    root = Path(__file__).resolve().parents[2]
    return {
        "command": sys.executable,
        "args": ["-m", "zhiwo.gateway.stdio_bridge"],
        "env": {
            "PYTHONPATH": str(root),
            "ZHIWO_API_ORIGIN": f"http://127.0.0.1:{port}",
        },
    }


def list_clients(home: Path, lookup=shutil.which) -> dict:
    return {
        "clients": [
            {
                "id": profile.id,
                "name": profile.name,
                "installed": _installed(home, profile, lookup),
                "configured": _configured(home, profile),
                "config_path": str(_config_path(home, profile.id)),
            }
            for profile in PROFILES
        ]
    }


def connect_client(
    db_path: Path,
    home: Path,
    client_id: str,
    preset: str,
    request_id: str,
    *,
    port: int,
) -> dict:
    profile = _BY_ID.get(client_id)
    if profile is None:
        raise ApiError(404, "NOT_FOUND", "没有这个客户端。")
    permissions = _PRESETS.get(preset)
    if permissions is None:
        raise ApiError(400, "VALIDATION_ERROR", "请选择只读，或允许提议修改。")
    digest = _digest({"client_id": client_id, "preset": preset})
    launch = bridge_launch(port)
    with commit_lock:
        prior = _prior(db_path, request_id)
        if prior is not None:
            if prior["action"] != "client_connect" or prior["payload_hash"] != digest or prior["status"] != "completed":
                raise ApiError(409, "CONFLICT", "这个请求号已经用过。")
            stored = json.loads(prior["payload_json"] or "{}")
            return stored
        agent_id, secret = _issue(db_path, profile)
        update_agent(
            db_path,
            agent_id,
            str(uuid.uuid4()),
            {**permissions, "enabled": True},
        )
        path = _config_path(home, profile.id)
        _write_profile(home, path, profile.id, launch, secret)
        result = {
            "client_id": profile.id,
            "name": profile.name,
            "agent_id": agent_id,
            "preset": preset,
            "config_path": str(path),
            "configured": True,
        }
        _remember(db_path, request_id, digest, result)
        return result


def _issue(db_path: Path, profile: ClientProfile) -> tuple[str, str]:
    key = f"client_agent:{profile.id}"
    agent_id = setting(db_path, key)
    if agent_id and _agent_exists(db_path, agent_id):
        issued = rotate_credential(db_path, agent_id, str(uuid.uuid4()))
    else:
        issued = create_agent(db_path, str(uuid.uuid4()), profile.name)
        agent_id = issued["id"]
        put_setting(db_path, key, agent_id)
    secret = issued.get("credential")
    if not isinstance(secret, str) or not secret:
        raise ApiError(500, "UNAVAILABLE", "这次没有生成可用凭证，配置没有写入。")
    return agent_id, secret


def _write_profile(home: Path, path: Path, client_id: str, launch: dict, secret: str) -> None:
    target = _contained(home, path)
    env = {**launch["env"], "ZHIWO_AGENT_CREDENTIAL": secret}
    if client_id == "codex":
        text = _codex_text(target, launch, env)
    else:
        text = _json_text(target, client_id, launch, env)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ApiError(500, "UNAVAILABLE", "配置没有写成。") from exc


def _json_text(path: Path, client_id: str, launch: dict, env: dict) -> str:
    data = _load_json(path)
    if client_id == "opencode":
        mcp = _object(data, "mcp")
        mcp["zhiwo"] = {
            "type": "local",
            "command": [launch["command"], *launch["args"]],
            "environment": env,
        }
    elif client_id == "zcode":
        mcp = _object(data, "mcp")
        servers = _object(mcp, "servers")
        servers["zhiwo"] = {"command": launch["command"], "args": list(launch["args"]), "env": env}
    elif client_id == "claude-code":
        servers = _object(data, "mcpServers")
        servers["zhiwo"] = {"type": "stdio", "command": launch["command"], "args": list(launch["args"]), "env": env}
    else:
        servers = _object(data, "mcpServers")
        servers["zhiwo"] = {"command": launch["command"], "args": list(launch["args"]), "env": env}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _codex_text(path: Path, launch: dict, env: dict) -> str:
    current = ""
    if path.is_file():
        current = path.read_text(encoding="utf-8")
    kept = _strip_codex_zhiwo(current).rstrip()
    args = ", ".join(_toml_string(item) for item in launch["args"])
    env_lines = "\n".join(f"{key} = {_toml_string(env[key])}" for key in sorted(env))
    block = (
        "[mcp_servers.zhiwo]\n"
        f"command = {_toml_string(launch['command'])}\n"
        f"args = [{args}]\n"
        "enabled = true\n"
        "\n"
        "[mcp_servers.zhiwo.env]\n"
        f"{env_lines}\n"
    )
    if not kept:
        return block
    return kept + "\n\n" + block


def _strip_codex_zhiwo(text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        header = line.strip()
        if header.startswith("[") and header.endswith("]"):
            name = header[1:-1].strip().strip('"')
            skipping = name == "mcp_servers.zhiwo" or name.startswith("mcp_servers.zhiwo.")
        if not skipping:
            kept.append(line)
    return "".join(kept)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "现有配置无法读取，没有改动。") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "VALIDATION_ERROR", "现有配置无法合并，没有改动。")
    return data


def _object(parent: dict, key: str) -> dict:
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    if not isinstance(value, dict):
        raise ApiError(400, "VALIDATION_ERROR", "现有配置无法合并，没有改动。")
    return value


def _config_path(home: Path, client_id: str) -> Path:
    if client_id == "workbuddy":
        return home / ".workbuddy" / "mcp.json"
    if client_id == "zcode":
        return home / ".zcode" / "cli" / "config.json"
    if client_id == "opencode":
        return home / ".config" / "opencode" / "opencode.json"
    if client_id == "codex":
        return home / ".codex" / "config.toml"
    if client_id == "claude":
        return home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    if client_id == "claude-code":
        return home / ".claude.json"
    raise ApiError(404, "NOT_FOUND", "没有这个客户端。")


def _contained(home: Path, path: Path) -> Path:
    home_root = home.resolve()
    if not _stays_inside(home_root, path):
        raise ApiError(400, "VALIDATION_ERROR", "配置路径超出了用户目录，没有写入。")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _stays_inside(home_root, path):
        raise ApiError(400, "VALIDATION_ERROR", "配置路径超出了用户目录，没有写入。")
    return path


def _stays_inside(home_root: Path, path: Path) -> bool:
    try:
        if path.is_symlink() or path.exists():
            return path.resolve().is_relative_to(home_root)
        parent = path.parent
        if parent == path:
            return False
        if not parent.exists():
            return parent.is_relative_to(home_root) or _stays_inside(home_root, parent)
        return (parent.resolve() / path.name).is_relative_to(home_root)
    except OSError:
        return False


def _installed(home: Path, profile: ClientProfile, lookup) -> bool:
    for marker in profile.markers:
        if (home / marker).exists():
            return True
    return lookup(profile.command_name) is not None


def _configured(home: Path, profile: ClientProfile) -> bool:
    path = _config_path(home, profile.id)
    if not path.is_file():
        return False
    try:
        if profile.id == "codex":
            return any(
                line.strip() in {"[mcp_servers.zhiwo]", '[mcp_servers."zhiwo"]'}
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if profile.id == "opencode":
        mcp = data.get("mcp")
        return isinstance(mcp, dict) and isinstance(mcp.get("zhiwo"), dict)
    if profile.id == "zcode":
        mcp = data.get("mcp")
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        return isinstance(servers, dict) and isinstance(servers.get("zhiwo"), dict)
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and isinstance(servers.get("zhiwo"), dict)


def _agent_exists(db_path: Path, agent_id: str) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute("SELECT 1 FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return row is not None
    finally:
        connection.close()


def _prior(db_path: Path, request_id: str):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT action, payload_hash, payload_json, status FROM operations WHERE id = ?",
            (request_id,),
        ).fetchone()
    finally:
        connection.close()


def _remember(db_path: Path, request_id: str, digest: str, result: dict) -> None:
    connection = sqlite3.connect(db_path)
    try:
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO operations (
                id, action, target_id, base_revision, payload_hash, payload_json,
                revision, kernel_id, status, error_code, created_at, updated_at
            ) VALUES (?, 'client_connect', ?, NULL, ?, ?, NULL, NULL, 'completed', NULL, ?, ?)
            """,
            (request_id, result["agent_id"], digest, json.dumps(result, ensure_ascii=False), now, now),
        )
        connection.commit()
    finally:
        connection.close()


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
