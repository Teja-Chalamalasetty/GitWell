"""In-process sliding-window attempt limiter (PRD S12). Valid because there is one process."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import Request

from app.config import Settings
from app.core.errors import AppError

WINDOW_SECONDS = 15 * 60
LOGIN_PER_IP_EMAIL = 5
PER_IP = 30
_SWEEP_THRESHOLD = 10_000


class AttemptLimiter:
    """Counts attempts per key inside a sliding window."""

    def __init__(
        self,
        limit: int,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and q[0] <= now - self.window:
            q.popleft()
        if not q:
            del self._hits[key]
            return deque()
        return q

    def retry_after(self, key: str) -> int | None:
        """Seconds until the key may try again, or None if it is under the limit."""
        with self._lock:
            now = self._clock()
            q = self._prune(key, now)
            if len(q) < self.limit:
                return None
            return max(1, math.ceil(q[0] + self.window - now))

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            if len(self._hits) > _SWEEP_THRESHOLD:
                for k in list(self._hits):
                    self._prune(k, now)
            self._prune(key, now)
            self._hits.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def too_many_attempts(retry_after: int) -> AppError:
    return AppError(
        429,
        "TOO_MANY_ATTEMPTS",
        "Too many attempts. Try again later.",
        {"retry_after_seconds": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


class AuthLimiters:
    """The limiters used by /auth endpoints; one instance per app."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.login_ip_email = AttemptLimiter(LOGIN_PER_IP_EMAIL, clock=clock)
        self.login_ip = AttemptLimiter(PER_IP, clock=clock)
        self.register_ip = AttemptLimiter(PER_IP, clock=clock)
