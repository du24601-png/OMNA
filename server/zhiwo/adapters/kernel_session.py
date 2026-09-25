"""Version-scoped Kernel session names.

Product scenario stays in Zhiwo control records. Kernel scope=global only
means a later recall can see these rows across sessions.
"""

from __future__ import annotations


def kernel_session(memory_id: str, revision: int) -> str:
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ValueError("memory_id is required")
    if not isinstance(revision, int) or revision < 1:
        raise ValueError("revision must start at 1")
    return f"zhiwo:{memory_id}:r{revision}"
