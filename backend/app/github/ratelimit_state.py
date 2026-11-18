"""Process-wide GitHub rate-limit state (PRD 12). Safe because there is exactly one process."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from datetime import UTC, datetime


class RateLimitState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.remaining: int | None = None
        self.reset_at: datetime | None = None

    def update(self, headers: Mapping[str, str]) -> None:
        raw_remaining = headers.get("x-ratelimit-remaining")
        raw_reset = headers.get("x-ratelimit-reset")
        try:
            remaining = int(raw_remaining) if raw_remaining is not None else None
            reset_at = (
                datetime.fromtimestamp(int(raw_reset), UTC) if raw_reset is not None else None
            )
        except (ValueError, OverflowError, OSError):
            return
        with self._lock:
            if remaining is not None:
                self.remaining = remaining
            if reset_at is not None:
                self.reset_at = reset_at

    def snapshot(self) -> tuple[int | None, datetime | None]:
        with self._lock:
            return self.remaining, self.reset_at

    def is_low(self, threshold: int = 50, now: datetime | None = None) -> bool:
        """True if the limit is nearly exhausted and has not reset yet (scheduler, PRD 14)."""
        remaining, reset_at = self.snapshot()
        now = now or datetime.now(UTC)
        return (
            remaining is not None
            and reset_at is not None
            and reset_at > now
            and remaining < threshold
        )

    def clear(self) -> None:
        with self._lock:
            self.remaining = None
            self.reset_at = None


rate_limit_state = RateLimitState()
