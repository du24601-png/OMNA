"""Local shell helpers for the Owner UI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from zhiwo.api.errors import ApiError
from zhiwo.config import ConfigError, validate_data_dir


def reveal_path(path: Path) -> None:
    target = path.expanduser().resolve()
    if not target.is_dir():
        raise ApiError(404, "NOT_FOUND", "这个文件夹不存在。")
    try:
        target = validate_data_dir(target)
    except ConfigError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "不能选择这个位置的文件夹。") from exc
    try:
        if sys.platform == "win32":
            os.startfile(target)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", target], check=True)
        else:
            subprocess.run(["xdg-open", target], check=True)
    except OSError as exc:
        raise ApiError(503, "UNAVAILABLE", "无法打开这个文件夹。", retryable=True) from exc


def pick_folder(initial: Path | None = None) -> Path | None:
    if sys.platform != "win32":
        raise ApiError(501, "NOT_IMPLEMENTED", "当前环境还不支持选择文件夹。")
    start = (initial or Path.home()).expanduser().resolve()
    if not start.is_dir():
        start = start.parent if start.parent.is_dir() else Path.home()
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$dialog.Description = '选择文件夹'; "
        f"$dialog.SelectedPath = '{_ps_quote(start)}'; "
        "$dialog.ShowNewFolderButton = $true; "
        "if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { "
        "Write-Output $dialog.SelectedPath }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        raise ApiError(503, "UNAVAILABLE", "无法打开文件夹选择器。", retryable=True) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ApiError(503, "UNAVAILABLE", detail or "无法打开文件夹选择器。", retryable=True)
    chosen = completed.stdout.strip()
    if not chosen:
        return None
    target = Path(chosen).expanduser().resolve()
    if not target.is_dir():
        raise ApiError(400, "VALIDATION_ERROR", "所选路径不是文件夹。")
    try:
        return validate_data_dir(target)
    except ConfigError as exc:
        raise ApiError(400, "VALIDATION_ERROR", "不能选择这个位置的文件夹。") from exc


def _ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")
