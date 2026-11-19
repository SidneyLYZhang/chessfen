from __future__ import annotations

import threading
import time
from collections import deque


class LoginRateLimiter:
    """Counts failed login attempts per client and blocks over a window."""

    def __init__(self, limit: int = 5, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def is_limited(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._active_events(client, now)
            return len(events) >= self._limit

    def record_failure(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._active_events(client, now)
            events.append(now)

    def reset(self, client: str) -> None:
        with self._lock:
            self._events.pop(client, None)

    def _active_events(self, client: str, now: float) -> deque[float]:
        events = self._events.setdefault(client, deque())
        while events and now - events[0] > self._window:
            events.popleft()
        return events
