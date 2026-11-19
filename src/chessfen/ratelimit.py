from __future__ import annotations

import threading
import time
from collections import deque


class _SlidingWindow:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _active(self, client: str, now: float) -> deque[float]:
        events = self._events.setdefault(client, deque())
        while events and now - events[0] > self._window:
            events.popleft()
        return events

    def reset(self, client: str) -> None:
        with self._lock:
            self._events.pop(client, None)


class LoginRateLimiter(_SlidingWindow):
    """Counts failed login attempts per client and blocks over a window."""

    def is_limited(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return len(self._active(client, now)) >= self._limit

    def record_failure(self, client: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._active(client, now).append(now)


class RequestRateLimiter(_SlidingWindow):
    """Allows at most ``limit`` requests per client within the window."""

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._active(client, now)
            if len(events) >= self._limit:
                return False
            events.append(now)
            return True