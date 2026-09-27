"""Owner API for opening a folder and choosing one in Explorer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from fastapi.testclient import TestClient

from zhiwo.api.app import app


class DataDirPickerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="zhiwo-picker-"))
        self.token = f"picker-{uuid.uuid4()}"
        self._saved = {
            name: os.environ.get(name)
            for name in (
                "ZHIWO_DATA_DIR",
                "ZHIWO_OWNER_CREDENTIAL",
                "ZHIWO_KERNEL_CONNECT_ONLY",
                "ZHIWO_TEST_MODE",
            )
        }
        os.environ["ZHIWO_DATA_DIR"] = str(self.directory)
        os.environ["ZHIWO_OWNER_CREDENTIAL"] = self.token
        os.environ["ZHIWO_KERNEL_CONNECT_ONLY"] = "1"
        os.environ["ZHIWO_TEST_MODE"] = "1"
        self.client = TestClient(app)
        self.client.__enter__()
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_requires_owner(self) -> None:
        opened = self.client.post("/api/v1/settings/data-dir/open", json={})
        picked = self.client.post("/api/v1/settings/data-dir/pick")
        self.assertEqual(opened.status_code, 401)
        self.assertEqual(picked.status_code, 401)

    def test_settings_reports_the_live_directory(self) -> None:
        response = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data_dir"], str(self.directory.resolve()))

    @patch("zhiwo.services.shell.os.startfile")
    def test_open_current_directory(self, startfile) -> None:
        response = self.client.post("/api/v1/settings/data-dir/open", headers=self.headers, json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["path"], str(self.directory.resolve()))
        startfile.assert_called_once_with(self.directory.resolve())

    @patch("zhiwo.services.shell.os.startfile")
    def test_open_rejects_missing_and_protected_paths(self, startfile) -> None:
        missing = self.client.post(
            "/api/v1/settings/data-dir/open",
            headers=self.headers,
            json={"path": str(self.directory / "missing")},
        )
        self.assertEqual(missing.status_code, 404)
        protected = self.client.post(
            "/api/v1/settings/data-dir/open",
            headers=self.headers,
            json={"path": str(Path(__file__).resolve().parents[1] / "experiments")},
        )
        self.assertEqual(protected.status_code, 400)
        self.assertEqual(protected.json()["error"]["code"], "VALIDATION_ERROR")
        startfile.assert_not_called()

    @patch("zhiwo.services.shell.subprocess.run")
    def test_pick_cancel_leaves_the_live_directory(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        picked = self.client.post("/api/v1/settings/data-dir/pick", headers=self.headers)
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(picked.json(), {"cancelled": True})
        current = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(current.json()["data_dir"], str(self.directory.resolve()))

    @patch("zhiwo.services.shell.subprocess.run")
    def test_pick_returns_folder_without_switching_library(self, run) -> None:
        chosen = self.directory / "chosen"
        chosen.mkdir()
        run.return_value.returncode = 0
        run.return_value.stdout = f"{chosen}\n"
        run.return_value.stderr = ""
        picked = self.client.post("/api/v1/settings/data-dir/pick", headers=self.headers)
        self.assertEqual(picked.status_code, 200)
        self.assertEqual(picked.json(), {"cancelled": False, "path": str(chosen.resolve())})
        current = self.client.get("/api/v1/settings", headers=self.headers)
        self.assertEqual(current.json()["data_dir"], str(self.directory.resolve()))

    @patch("zhiwo.services.shell.subprocess.run")
    def test_pick_rejects_protected_folder(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = f"{Path(__file__).resolve().parents[1] / 'experiments'}\n"
        run.return_value.stderr = ""
        picked = self.client.post("/api/v1/settings/data-dir/pick", headers=self.headers)
        self.assertEqual(picked.status_code, 400)
        self.assertEqual(picked.json()["error"]["code"], "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
