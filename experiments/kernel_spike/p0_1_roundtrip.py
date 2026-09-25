"""P0.1: pin Mnemosyne, write one Chinese memory, restart, read it back.

Uses an isolated directory under experiments/kernel_spike/runs.
Does not open ~/.hermes or any pre-existing Mnemosyne database.
Semantic recall, embeddings, and the product UI are out of scope.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SAMPLE = "知我 P0.1 合成样本：日常回复偏好简洁。标记 ZW-P01-SYNTH-7F3A。"
SESSION_ID = "zhiwo-p0-1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SPIKE_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = SPIKE_ROOT / "runs" / "p0_1"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_1_windows.json"

DROPPED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "HERMES_HOME",
    "MNEMOSYNE_HOME",
    "MNEMOSYNE_DATA_DIR",
    "MNEMOSYNE_DB_PATH",
    "MNEMOSYNE_EMBEDDING_API_KEY",
    "MNEMOSYNE_EMBEDDING_API_URL",
    "MNEMOSYNE_LLM_API_KEY",
    "MNEMOSYNE_LLM_BASE_URL",
    "MNEMOSYNE_SYNC_REMOTE",
    "MNEMOSYNE_SYNC_KEY",
)


def assert_native_windows() -> None:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise SystemExit(
            "BLOCKED: P0.1 requires native Windows. "
            f"sys.platform={sys.platform} system={platform.system()}"
        )
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        raise SystemExit("BLOCKED: WSL is not accepted for P0.1 Windows evidence.")


def assert_isolated(data_dir: Path) -> Path:
    resolved = data_dir.resolve()
    runs = (SPIKE_ROOT / "runs").resolve()
    if resolved != runs and runs not in resolved.parents:
        raise SystemExit(f"Refusing data dir outside spike runs: {resolved}")
    home = Path.home().resolve()
    for forbidden in (home / ".hermes", home / ".mnemosyne"):
        if resolved == forbidden or forbidden in resolved.parents:
            raise SystemExit(f"Refusing to touch user Mnemosyne data: {resolved}")
    return resolved


def child_env(data_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in DROPPED_ENV}
    env.update(
        {
            "MNEMOSYNE_DATA_DIR": str(data_dir),
            "MNEMOSYNE_NO_EMBEDDINGS": "1",
            "MNEMOSYNE_EMBEDDINGS_OFF": "1",
            "MNEMOSYNE_SKIP_EMBEDDINGS": "1",
            "MNEMOSYNE_EMBEDDINGS_VIA_API": "0",
            "MNEMOSYNE_LLM_ENABLED": "0",
            "MNEMOSYNE_HOST_LLM_ENABLED": "0",
            "MNEMOSYNE_FORCE_LOCAL": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def db_path_for(data_dir: Path) -> Path:
    return data_dir / "mnemosyne.db"


def snapshot_user_hermes() -> dict[str, float | None]:
    root = Path.home() / ".hermes"
    if not root.exists():
        return {"exists": None}
    latest = None
    for path in root.rglob("*"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        latest = mtime if latest is None else max(latest, mtime)
    return {"exists": root.stat().st_mtime, "latest_child": latest}


def run_mode(mode: str, data_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(data_dir)],
        cwd=str(SPIKE_ROOT),
        env=child_env(data_dir),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def write_memory(data_dir: Path) -> None:
    os.environ.update(child_env(data_dir))
    from mnemosyne import Mnemosyne

    db_path = db_path_for(data_dir)
    mem = Mnemosyne(session_id=SESSION_ID, db_path=db_path)
    memory_id = mem.remember(
        SAMPLE,
        source="zhiwo-p0.1",
        importance=0.9,
        scope="global",
        extract=False,
        extract_entities=False,
    )
    if not memory_id:
        raise SystemExit("remember() returned no id; write was filtered or failed")
    if Path(mem.db_path).resolve() != db_path.resolve():
        raise SystemExit(f"Kernel opened unexpected database: {mem.db_path}")
    payload = {
        "memory_id": memory_id,
        "content": SAMPLE,
        "db_path": str(db_path.resolve()),
        "mnemosyne_version": __import__("mnemosyne").__version__,
    }
    (data_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))


def read_memory(data_dir: Path) -> None:
    os.environ.update(child_env(data_dir))
    from mnemosyne import Mnemosyne

    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    db_path = db_path_for(data_dir)
    mem = Mnemosyne(session_id=SESSION_ID, db_path=db_path)
    row = mem.get(manifest["memory_id"])
    content = row.get("content") if isinstance(row, dict) else None
    payload = {
        "memory_id": manifest["memory_id"],
        "content": content,
        "match": content == SAMPLE,
        "db_path": str(Path(mem.db_path).resolve()),
        "found": row is not None,
    }
    print(json.dumps(payload, ensure_ascii=False))
    if content != SAMPLE or Path(mem.db_path).resolve() != db_path.resolve():
        raise SystemExit(1)


def tool_version(command: list[str]) -> str:
    candidates = [command]
    if os.name == "nt" and not command[0].lower().endswith(".cmd"):
        candidates.append([f"{command[0]}.cmd", *command[1:]])
    last_error = "not found"
    for candidate in candidates:
        try:
            completed = subprocess.run(
                candidate,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            last_error = str(exc)
            continue
        text = (completed.stdout or completed.stderr).strip()
        if text:
            return text.splitlines()[0]
        last_error = f"exit {completed.returncode}"
    return f"NOT_RUN: {last_error}"
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0] if text else f"exit {completed.returncode}"


def main() -> int:
    assert_native_windows()
    if len(sys.argv) == 3 and sys.argv[1] in {"write", "read"}:
        data_dir = assert_isolated(Path(sys.argv[2]))
        if sys.argv[1] == "write":
            write_memory(data_dir)
        else:
            read_memory(data_dir)
        return 0

    data_dir = assert_isolated(DEFAULT_DATA_DIR)
    before_hermes = snapshot_user_hermes()
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    write_proc = run_mode("write", data_dir)
    read_proc = run_mode("read", data_dir)
    after_hermes = snapshot_user_hermes()

    def parse_stdout(proc: subprocess.CompletedProcess[str]) -> dict | None:
        text = (proc.stdout or "").strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    write_payload = parse_stdout(write_proc)
    read_payload = parse_stdout(read_proc)
    matched = bool(read_payload and read_payload.get("match") is True)
    status = "DONE" if write_proc.returncode == 0 and read_proc.returncode == 0 and matched else "FAILED"

    import mnemosyne

    result = {
        "task": "P0.1",
        "status": status,
        "native_windows": True,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "versions": {
            "python": sys.version.split()[0],
            "python_executable": sys.executable,
            "mnemosyne_memory": mnemosyne.__version__,
            "mnemosyne_tag": "v3.15.1",
            "mnemosyne_commit": "78506708aae344635e01a24f67a7319efc36fce9",
            "uv": tool_version(["uv", "--version"]),
            "node": tool_version(["node", "--version"]),
            "pnpm": tool_version(["pnpm", "--version"]),
        },
        "commands": [
            "uv python pin 3.12",
            "uv lock",
            "uv sync",
            "uv run python p0_1_roundtrip.py",
        ],
        "child_commands": [
            [sys.executable, "p0_1_roundtrip.py", "write", str(data_dir)],
            [sys.executable, "p0_1_roundtrip.py", "read", str(data_dir)],
        ],
        "exit_codes": {"write": write_proc.returncode, "read": read_proc.returncode},
        "data_dir": str(data_dir),
        "db_path": str(db_path_for(data_dir)),
        "sample": SAMPLE,
        "write": write_payload,
        "read_back": read_payload,
        "user_hermes_mtime_unchanged": before_hermes == after_hermes,
        "user_hermes_before": before_hermes,
        "user_hermes_after": after_hermes,
        "not_verified": [
            "semantic recall and Chinese Hit@5 (P0.3)",
            "local embedding model and offline recall (P0.3)",
            "version history, delete, expiry, idempotent operation_id (P0.2)",
            "stdio MCP client (P0.4)",
            "product UI, zhiwo.db, Electron packaging",
        ],
        "stderr": {
            "write": write_proc.stderr[-4000:],
            "read": read_proc.stderr[-4000:],
        },
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "result": str(RESULT_PATH), "match": matched}, ensure_ascii=False))
    if write_proc.returncode != 0:
        print(write_proc.stderr, file=sys.stderr)
    if read_proc.returncode != 0:
        print(read_proc.stderr, file=sys.stderr)
    return 0 if status == "DONE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
