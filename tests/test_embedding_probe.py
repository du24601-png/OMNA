"""The startup embedding probe must leave the reason in the service log.

Mnemosyne's embed() returns None when fastembed cannot be imported (for
example onnxruntime missing msvcp140.dll), so the cause would otherwise be lost.
"""

from __future__ import annotations

import builtins
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.adapters import kernel_client  # noqa: E402


class _NoVector:
    @staticmethod
    def embed(_texts):
        return None


class _Raises:
    @staticmethod
    def embed(_texts):
        raise RuntimeError("model files are missing")


class _Works:
    @staticmethod
    def embed(texts):
        return [[0.1, 0.2] for _ in texts]


class EmbeddingProbeTest(unittest.TestCase):
    def test_import_failure_is_logged(self) -> None:
        real_import = builtins.__import__

        def fail_fastembed(name, *args, **kwargs):
            if name == "fastembed" or name.startswith("fastembed."):
                raise ImportError("DLL load failed while importing onnxruntime_pybind11_state: 找不到指定的模块。")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=fail_fastembed):
            with self.assertLogs("zhiwo.adapters.kernel_client", level="WARNING") as logs:
                self.assertFalse(kernel_client._probe_embeddings(_NoVector))
        self.assertIn("onnxruntime_pybind11_state", "\n".join(logs.output))

    def test_embed_exception_is_logged(self) -> None:
        with self.assertLogs("zhiwo.adapters.kernel_client", level="WARNING") as logs:
            self.assertFalse(kernel_client._probe_embeddings(_Raises))
        self.assertIn("model files are missing", "\n".join(logs.output))

    def test_working_model_is_quiet(self) -> None:
        with self.assertNoLogs("zhiwo.adapters.kernel_client", level="WARNING"):
            self.assertTrue(kernel_client._probe_embeddings(_Works))


if __name__ == "__main__":
    unittest.main()
