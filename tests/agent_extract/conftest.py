"""MOCK KERNEL for tests/agent_extract only.

Replaces the Mnemosyne-backed adapter functions with a small SQLite store
that has the same tables the adapter reads directly. Control DB, policy,
audit, review, HTTP and the stdio bridge stay real. Results from this
directory are not Kernel or real-client acceptance evidence (AGENTS.md §5).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

if os.environ.get("ZHIWO_REAL_KERNEL") != "1":
    import fakekernel

    fakekernel.install()
