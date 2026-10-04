"""Write one local stdio bridge entry into the known clients.

The bridge and the four tools stay unchanged. This module only detects a
fixed install and merges a single `omna` server into that client's own
config. It never replaces the rest of the file, and it never returns the
credential.

Detection stays on the six known clients. It looks at their config
locations, the command name, the user and machine PATH, and a few install
files under the user profile. It does not scan the disk or running processes.

The launch command is the Python that is running this service. A packaged
app can point it at the bundled interpreter with ZHIWO_BRIDGE_PYTHON and,
when the package is not already importable, ZHIWO_BRIDGE_PYTHONPATH.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tomllib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from zhiwo.api.auth import install_redaction
from zhiwo.api.errors import ApiError
from zhiwo.contracts.memory import CATEGORIES, TOOLS
from zhiwo.repositories.migrate import put_setting, setting
from zhiwo.services.agents import commit_credential, create_agent, update_agent
from zhiwo.services.commit_gate import commit_lock

READ_CATEGORIES = ("preference", "goal")
READ_TOOLS = ("get_context", "search_memory")
PROPOSE_TOOLS = (*READ_TOOLS, "propose_memory")
_MCP_NAMES = ("omna", "zhiwo")  # Accept old configs; rewrites publish only omna.
# Reads stay narrow. A connection that may propose can suggest any category,
# because every proposal waits for the owner's review before it counts.
_PRESETS = {
    "read": {"allowed_categories": list(READ_CATEGORIES), "allowed_tools": list(READ_TOOLS), "propose_categories": []},
    "propose": {
        "allowed_categories": list(READ_CATEGORIES),
        "allowed_tools": list(PROPOSE_TOOLS),
        "propose_categories": list(CATEGORIES),
    },
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
    command, pythonpath = _bridge_runtime()
    env = {"ZHIWO_API_ORIGIN": f"http://127.0.0.1:{port}"}
    args = ["-m", "zhiwo.gateway.stdio_bridge"]
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    else:
        # -I keeps the client's PYTHON* variables and user site-packages from
        # shadowing the installed packages; it also drops PYTHONUTF8.
        args = ["-I", "-X", "utf8", *args]
    return {"command": command, "args": args, "env": env}


def _bridge_runtime() -> tuple[str, str | None]:
    """Python and optional code directory for the stdio bridge.

    Source checkouts keep the server directory on PYTHONPATH. A packaged
    process sets ZHIWO_BRIDGE_PYTHON to the bundled interpreter. Set
    ZHIWO_BRIDGE_PYTHONPATH only when that interpreter cannot import zhiwo.
    """
    python = os.environ.get("ZHIWO_BRIDGE_PYTHON", "").strip()
    root = os.environ.get("ZHIWO_BRIDGE_PYTHONPATH", "").strip()
    if python or root:
        executable = _env_path(python) if python else None
        if executable is None or not executable.is_file():
            raise ApiError(500, "UNAVAILABLE", "随包 Python 不可用，没有写入配置。")
        if not root:
            return str(executable.resolve()), None
        directory = _env_path(root)
        if not directory.is_dir():
            raise ApiError(500, "UNAVAILABLE", "随包代码目录不可用，没有写入配置。")
        return str(executable.resolve()), str(directory.resolve())
    source = Path(__file__).resolve().parents[2]
    bridge = source / "zhiwo" / "gateway" / "stdio_bridge.py"
    packaged = "site-packages" in {part.lower() for part in Path(__file__).resolve().parts}
    if bridge.is_file() and not packaged:
        return sys.executable, str(source)
    return sys.executable, None


def _env_path(raw: str) -> Path:
    return Path(os.path.expandvars(raw)).expanduser()


def list_clients(
    home: Path,
    lookup=shutil.which,
    db_path: Path | None = None,
    path_lookup=None,
    packages: frozenset[str] | None = None,
) -> dict:
    scan_machine = path_lookup is None and packages is None and lookup is shutil.which and sys.platform == "win32"
    user_path = _windows_user_path() if scan_machine else ""
    found = frozenset({"claude"}) if scan_machine and _claude_appx_installed() else frozenset()
    if packages is not None:
        found = packages
    return {
        "clients": [
            {
                "id": profile.id,
                "name": profile.name,
                "installed": _installed(home, profile, lookup, path_lookup, user_path, found),
                "configured": _configured(home, profile),
                "config_path": str(_config_path(home, profile.id)),
                "agent_id": _linked_agent(db_path, profile) if db_path is not None else None,
            }
            for profile in PROFILES
        ]
    }


def _linked_agent(db_path: Path, profile: ClientProfile) -> str | None:
    agent_id = setting(db_path, f"client_agent:{profile.id}")
    if agent_id and _agent_exists(db_path, agent_id):
        return agent_id
    return None


def connect_client(
    db_path: Path,
    home: Path,
    client_id: str,
    preset: str,
    request_id: str,
    *,
    port: int,
    allowed_tools: list[str] | None = None,
    allowed_categories: list[str] | None = None,
    propose_categories: list[str] | None = None,
) -> dict:
    profile = _BY_ID.get(client_id)
    if profile is None:
        raise ApiError(404, "NOT_FOUND", "没有这个客户端。")
    permissions = _resolved_permissions(preset, allowed_tools, allowed_categories, propose_categories)
    payload = {"client_id": client_id, "preset": preset}
    if allowed_tools is not None:
        payload["allowed_tools"] = permissions["allowed_tools"]
        payload["allowed_categories"] = permissions["allowed_categories"]
    if propose_categories is not None:
        payload["propose_categories"] = permissions["propose_categories"]
    digest = _digest(payload)
    with commit_lock:
        prior = _prior(db_path, request_id)
        if prior is not None:
            if prior["action"] != "client_connect" or prior["payload_hash"] != digest or prior["status"] != "completed":
                raise ApiError(409, "CONFLICT", "这个请求号已经用过。")
            stored = json.loads(prior["payload_json"] or "{}")
            return stored
        launch = bridge_launch(port)
        path = _config_path(home, profile.id)
        _prepare_target(home, path, profile.id)
        agent_id = _existing_agent(db_path, profile)
        secret = secrets.token_urlsafe(32)
        install_redaction(secret)
        _write_profile(home, path, profile.id, launch, secret)
        if agent_id is None:
            issued = create_agent(db_path, str(uuid.uuid4()), profile.name, secret=secret)
            agent_id = issued["id"]
            put_setting(db_path, f"client_agent:{profile.id}", agent_id)
        else:
            commit_credential(db_path, agent_id, secret)
        update_agent(
            db_path,
            agent_id,
            str(uuid.uuid4()),
            {**permissions, "enabled": True},
        )
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


def _resolved_permissions(preset: str, allowed_tools, allowed_categories, propose_categories=None) -> dict:
    base = _PRESETS.get(preset)
    if base is None:
        raise ApiError(400, "VALIDATION_ERROR", "请选择只读，或允许提议修改。")
    if allowed_tools is None and allowed_categories is None:
        resolved = {
            "allowed_categories": list(base["allowed_categories"]),
            "allowed_tools": list(base["allowed_tools"]),
            "propose_categories": list(base["propose_categories"]),
        }
    elif allowed_tools is None or allowed_categories is None:
        raise ApiError(400, "VALIDATION_ERROR", "重写配置时需要同时给出工具和类别。")
    else:
        tools = _choice_list(allowed_tools, TOOLS, "tool")
        resolved = {
            "allowed_tools": tools,
            "allowed_categories": _choice_list(allowed_categories, CATEGORIES, "category"),
            "propose_categories": list(CATEGORIES) if "propose_memory" in tools else [],
        }
    if propose_categories is not None:
        resolved["propose_categories"] = _choice_list(propose_categories, CATEGORIES, "category")
    return resolved


def _choice_list(value, allowed: tuple[str, ...], label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ApiError(400, "VALIDATION_ERROR", f"{label} list is invalid")
    if len(value) != len(set(value)):
        raise ApiError(400, "VALIDATION_ERROR", f"duplicate {label}")
    unknown = [item for item in value if item not in allowed]
    if unknown:
        raise ApiError(400, "VALIDATION_ERROR", f"{label} is not supported")
    return list(value)


def _prepare_target(home: Path, path: Path, client_id: str) -> None:
    try:
        _contained(home, path)
    except OSError as exc:
        raise ApiError(500, "UNAVAILABLE", "配置没有写成。") from exc
    if client_id != "codex":
        _load_json(path)


def _existing_agent(db_path: Path, profile: ClientProfile) -> str | None:
    agent_id = setting(db_path, f"client_agent:{profile.id}")
    if agent_id and _agent_exists(db_path, agent_id):
        return agent_id
    return None


def _write_profile(home: Path, path: Path, client_id: str, launch: dict, secret: str) -> None:
    env = {**launch["env"], "ZHIWO_AGENT_CREDENTIAL": secret}
    temporary: Path | None = None
    try:
        target = _contained(home, path)
        if client_id == "codex":
            text = _codex_text(target, launch, env)
        else:
            text = _json_text(target, client_id, launch, env)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        temporary.replace(target)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise ApiError(500, "UNAVAILABLE", "配置没有写成。") from exc


def _json_text(path: Path, client_id: str, launch: dict, env: dict) -> str:
    data = _load_json(path)
    if client_id == "opencode":
        mcp = _object(data, "mcp")
        mcp.pop("zhiwo", None)
        mcp["omna"] = {
            "type": "local",
            "command": [launch["command"], *launch["args"]],
            "environment": env,
        }
    elif client_id == "zcode":
        mcp = _object(data, "mcp")
        servers = _object(mcp, "servers")
        servers.pop("zhiwo", None)
        servers["omna"] = {"command": launch["command"], "args": list(launch["args"]), "env": env}
    elif client_id == "claude-code":
        servers = _object(data, "mcpServers")
        servers.pop("zhiwo", None)
        servers["omna"] = {"type": "stdio", "command": launch["command"], "args": list(launch["args"]), "env": env}
    else:
        servers = _object(data, "mcpServers")
        servers.pop("zhiwo", None)
        servers["omna"] = {"command": launch["command"], "args": list(launch["args"]), "env": env}
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _codex_text(path: Path, launch: dict, env: dict) -> str:
    current = ""
    if path.is_file():
        current = path.read_text(encoding="utf-8")
    kept = _strip_codex_memory_servers(current).rstrip()
    args = ", ".join(_toml_string(item) for item in launch["args"])
    env_lines = "\n".join(f"{key} = {_toml_string(env[key])}" for key in sorted(env))
    block = (
        "[mcp_servers.omna]\n"
        f"command = {_toml_string(launch['command'])}\n"
        f"args = [{args}]\n"
        "enabled = true\n"
        "\n"
        "[mcp_servers.omna.env]\n"
        f"{env_lines}\n"
    )
    if not kept:
        return block
    return kept + "\n\n" + block


def _strip_codex_memory_servers(text: str) -> str:
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        header = line.strip()
        if header.startswith("[") and header.endswith("]"):
            name = header[1:-1].strip()
            skipping = re.fullmatch(
                r'''mcp_servers\s*\.\s*(?:omna|zhiwo|"omna"|"zhiwo"|'omna'|'zhiwo')(?:\s*\..+)?''',
                name,
            ) is not None
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


# Install files that live under the user profile even when the service
# process does not inherit the user's PATH. Names follow the installers.
_INSTALL_FILES = {
    "workbuddy": (
        "AppData/Local/Programs/WorkBuddy/WorkBuddy.exe",
        "AppData/Local/WorkBuddy/WorkBuddy.exe",
    ),
    "zcode": (
        "AppData/Local/Programs/ZCode/ZCode.exe",
        "AppData/Local/Programs/zcode/zcode.exe",
    ),
    "opencode": (
        "AppData/Roaming/npm/opencode.cmd",
        "AppData/Roaming/npm/opencode.exe",
        "AppData/Roaming/npm/node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe",
        ".local/bin/opencode.exe",
    ),
    "codex": (
        "AppData/Local/Programs/ChatGPT/ChatGPT.exe",
        "AppData/Roaming/npm/codex.cmd",
        ".local/bin/codex.exe",
    ),
    "claude": (
        "AppData/Local/AnthropicClaude/claude.exe",
        "AppData/Local/Programs/Claude/Claude.exe",
        "AppData/Local/Programs/claude-desktop/Claude.exe",
    ),
    "claude-code": (
        "AppData/Roaming/npm/claude.cmd",
        ".local/bin/claude.exe",
        ".claude/local/claude.exe",
    ),
}


def _installed(home: Path, profile: ClientProfile, lookup, path_lookup, user_path: str, packages: frozenset[str]) -> bool:
    for marker in profile.markers:
        if (home / marker).exists():
            return True
    if lookup(profile.command_name) is not None:
        return True
    if _known_install(home, profile):
        return True
    if path_lookup is not None and path_lookup(profile.command_name) is not None:
        return True
    if user_path and shutil.which(profile.command_name, path=user_path) is not None:
        return True
    return profile.id in packages


def _known_install(home: Path, profile: ClientProfile) -> bool:
    for relative in _INSTALL_FILES.get(profile.id, ()):
        if _file_inside(home, home.joinpath(*relative.split("/"))):
            return True
    if profile.id != "claude":
        return False
    root = home / "AppData" / "Local" / "AnthropicClaude"
    if not _dir_inside(home, root):
        return False
    try:
        children = list(root.iterdir())
    except OSError:
        return False
    return any(
        child.is_dir() and child.name.startswith("app-") and _file_inside(home, child / "claude.exe")
        for child in children
    )


def _file_inside(home: Path, candidate: Path) -> bool:
    try:
        if not candidate.is_file():
            return False
        return candidate.resolve().is_relative_to(home.resolve())
    except OSError:
        return False


def _dir_inside(home: Path, candidate: Path) -> bool:
    try:
        if not candidate.is_dir():
            return False
        return candidate.resolve().is_relative_to(home.resolve())
    except OSError:
        return False


def _windows_user_path() -> str:
    if sys.platform != "win32":
        return ""
    import winreg

    chunks: list[str] = []
    queries = (
        (winreg.HKEY_CURRENT_USER, r"Environment", "Path"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Path"),
    )
    for hive, key, name in queries:
        try:
            with winreg.OpenKey(hive, key) as handle:
                value, _kind = winreg.QueryValueEx(handle, name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            chunks.append(os.path.expandvars(value))
    return os.pathsep.join(chunks)


def _claude_appx_installed() -> bool:
    """Claude Desktop from the Store or the MSIX installer.

    The package has no stable exe under the user profile. The family name
    is registered for the current user. This does not read WindowsApps.
    """
    if sys.platform != "win32":
        return False
    import winreg

    families = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel\Repository\Families"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, families) as key:
            count = winreg.QueryInfoKey(key)[0]
            for index in range(count):
                name = winreg.EnumKey(key, index)
                if name.startswith("Claude_") and name.endswith("pzs8sxrjxfjjc"):
                    return True
    except OSError:
        return False
    return False


def _configured(home: Path, profile: ClientProfile) -> bool:
    path = _config_path(home, profile.id)
    if not path.is_file():
        return False
    try:
        if profile.id == "codex":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if profile.id == "codex":
        servers = data.get("mcp_servers")
        return isinstance(servers, dict) and any(isinstance(servers.get(name), dict) for name in _MCP_NAMES)
    if profile.id == "opencode":
        mcp = data.get("mcp")
        return isinstance(mcp, dict) and any(isinstance(mcp.get(name), dict) for name in _MCP_NAMES)
    if profile.id == "zcode":
        mcp = data.get("mcp")
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        return isinstance(servers, dict) and any(isinstance(servers.get(name), dict) for name in _MCP_NAMES)
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and any(isinstance(servers.get(name), dict) for name in _MCP_NAMES)


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
