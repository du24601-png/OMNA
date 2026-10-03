"""Check the real CLI's output with a synthetic HTTP endpoint, no user library."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("dev_seed.py")


class DevSeedOutputTest(unittest.TestCase):
    def run_propose(self, status: int = 200):
        submissions = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                submissions.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps({"status": "pending"} if status == 200 else {"error": "合成服务拒绝"}, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

        with tempfile.TemporaryDirectory(prefix="omna-seed-output-") as directory:
            folder = Path(directory)
            (folder / "owner.credential").write_text("synthetic-owner", encoding="utf-8")
            (folder / "dev-seed.json").write_text(json.dumps({"OpenCode": "synthetic-agent"}), encoding="utf-8")
            with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    completed = subprocess.run(
                        [sys.executable, str(SCRIPT), "propose", "--port", str(server.server_port), "--user-data", str(folder)],
                        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
                        capture_output=True,
                        timeout=15,
                    )
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        return completed, submissions

    def test_successful_submission_exits_zero_and_prints_chinese(self):
        completed, submissions = self.run_propose()
        self.assertEqual(len(submissions), 1)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertIn(submissions[0]["change"]["content"], completed.stdout.decode("utf-8"))

    def test_http_failure_remains_visible_in_chinese(self):
        completed, submissions = self.run_propose(503)
        self.assertEqual(len(submissions), 1)
        self.assertNotEqual(completed.returncode, 0)
        error = completed.stderr.decode("utf-8")
        self.assertIn("合成服务拒绝", error)
        self.assertIn("HTTP Error 503", error)
        self.assertNotIn("UnicodeEncodeError", error)


if __name__ == "__main__":
    unittest.main()
