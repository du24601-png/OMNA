"""One process lock for control changes and tool responses.

Vector recall stays outside the lock. Hold it while a control transaction
commits, while a tool re-checks authorization and commits its access
snapshot, and while that snapshot is handed to the HTTP send channel.
A revocation waits for the same lock, so it cannot land between the last
check and the channel write.
"""

from __future__ import annotations

import threading

commit_lock = threading.RLock()
