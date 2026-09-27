"""Shell helpers for opening and picking local folders."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.api.errors import ApiError
from zhiwo.config import ConfigError, validate_data_dir
from zhiwo.services.shell import pick_folder, reveal_path


class ShellTest(unittest.TestCase):
    def test_validate_data_dir_rejects_repo_experiments(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        with self.assertRaises(ConfigError):
            validate_data_dir(repo / "experiments")

    def test_reveal_path_requires_existing_dir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw) / "missing"
            with self.assertRaises(ApiError) as ctx:
                reveal_path(missing)
            self.assertEqual(ctx.exception.code, "NOT_FOUND")

    @patch("zhiwo.services.shell.os.startfile")
    def test_reveal_path_opens_dir_on_windows(self, startfile) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows-only open helper")
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw)
            reveal_path(target)
            startfile.assert_called_once_with(target.resolve())

    @patch("zhiwo.services.shell.subprocess.run")
    def test_pick_folder_returns_none_when_cancelled(self, run) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows-only folder picker")
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            run.return_value.returncode = 0
            run.return_value.stdout = ""
            self.assertIsNone(pick_folder(Path(raw)))

    @patch("zhiwo.services.shell.subprocess.run")
    def test_pick_folder_returns_validated_path(self, run) -> None:
        if sys.platform != "win32":
            self.skipTest("Windows-only folder picker")
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw)
            run.return_value.returncode = 0
            run.return_value.stdout = f"{target}\n"
            self.assertEqual(pick_folder(target), target.resolve())


if __name__ == "__main__":
    unittest.main()
