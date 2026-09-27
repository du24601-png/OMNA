"""Process configuration. Secrets stay in the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LISTEN_HOST = "127.0.0.1"


class ConfigError(RuntimeError):
    """The process cannot start with the current environment."""


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    owner_credential: str
    connect_only: bool
    fastembed_cache: Path
    extractor_base_url: str
    extractor_model: str
    extractor_api_key: str
    test_mode: bool
    host: str = LISTEN_HOST

    @property
    def control_db(self) -> Path:
        return self.data_dir / "zhiwo.db"

    @property
    def kernel_dir(self) -> Path:
        return self.data_dir / "kernel"

    @property
    def extractor_configured(self) -> bool:
        return bool(self.extractor_base_url and self.extractor_model and self.extractor_api_key)


def load_settings() -> Settings:
    raw_dir = os.environ.get("ZHIWO_DATA_DIR", "").strip()
    credential = os.environ.get("ZHIWO_OWNER_CREDENTIAL", "")
    host = os.environ.get("ZHIWO_HOST", LISTEN_HOST).strip()
    if not raw_dir:
        raise ConfigError("ZHIWO_DATA_DIR is required")
    if not credential:
        raise ConfigError("ZHIWO_OWNER_CREDENTIAL is required")
    if host != LISTEN_HOST:
        raise ConfigError("the service listens on 127.0.0.1 only")
    data_dir = Path(raw_dir).expanduser().resolve()
    _reject_protected_dir(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_raw = os.environ.get("ZHIWO_FASTEMBED_CACHE_DIR", "").strip()
    cache_dir = Path(cache_raw).expanduser().resolve() if cache_raw else data_dir / "kernel" / "fastembed-cache"
    return Settings(
        data_dir=data_dir,
        owner_credential=credential,
        connect_only=os.environ.get("ZHIWO_KERNEL_CONNECT_ONLY", "").strip() == "1",
        fastembed_cache=cache_dir,
        extractor_base_url=os.environ.get("ZHIWO_EXTRACTOR_BASE_URL", "").strip(),
        extractor_model=os.environ.get("ZHIWO_EXTRACTOR_MODEL", "").strip(),
        extractor_api_key=os.environ.get("ZHIWO_EXTRACTOR_API_KEY", ""),
        test_mode=os.environ.get("ZHIWO_TEST_MODE", "").strip() == "1",
        host=host,
    )


def validate_data_dir(data_dir: Path) -> Path:
    resolved = data_dir.expanduser().resolve()
    _reject_protected_dir(resolved)
    return resolved


def _reject_protected_dir(data_dir: Path) -> None:
    for root in _protected_roots():
        if _is_inside(data_dir, root):
            raise ConfigError("ZHIWO_DATA_DIR must be an independent directory")


def _protected_roots() -> list[Path]:
    roots = [
        REPO_ROOT / "experiments",
        Path.home() / ".hermes",
        Path.home() / ".mnemosyne",
    ]
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        roots.append(Path(local_app) / "hermes")
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        roots.append(Path(hermes_home))
    return roots


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True
