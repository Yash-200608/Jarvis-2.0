"""
Sliding-window, per-key rate limiter shared by the legacy HTTP bridges
(phone_bridge.py and bridge_server.py).

The bridges authenticate with a single bearer token. This adds a cheap second
line of defence: a caller that is guessing tokens, or a runaway loop, gets
throttled instead of being free to hammer /execute.
"""

import threading
import time
from collections import deque


class RateLimiter:
    """Allow at most `max_requests` per `window_seconds` for each key (IP)."""

    def __init__(self, max_requests=60, window_seconds=60):
        if max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.max_requests = int(max_requests)
        self.window_seconds = float(window_seconds)
        self._hits = {}  # key -> deque of monotonic timestamps
        self._lock = threading.Lock()

    def is_allowed(self, key):
        """Record a request for `key`; return False if it is over the limit.

        Rejected requests are not recorded, so a throttled caller recovers as
        soon as its oldest allowed request leaves the window.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            self._prune(cutoff)
            return True

    def _prune(self, cutoff):
        # Drop keys whose window is entirely expired so the dict can't grow
        # without bound under many distinct source IPs. Called under the lock.
        if len(self._hits) < 1024:
            return
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
