"""Cooperative scan deadline (PRD 8.1). Python threads cannot be killed, so code checks this."""

from __future__ import annotations

import time
from collections.abc import Callable


class ScanTimeout(Exception):
    """The scan exceeded its time limit."""


class Deadline:
    def __init__(self, seconds: float | None, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._at = float("inf") if seconds is None else clock() + seconds

    @classmethod
    def none(cls) -> Deadline:
        """A deadline that never expires (until `expire()` is called)."""
        return cls(None)

    def remaining(self) -> float:
        return self._at - self._clock()

    def expired(self) -> bool:
        return self.remaining() <= 0

    def check(self) -> None:
        if self.expired():
            raise ScanTimeout

    def expire(self) -> None:
        """Make every later `check()` raise (used by graceful shutdown)."""
        self._at = self._clock()

    def clamp(self, timeout: float) -> float:
        """`min(timeout, remaining)`, raising ScanTimeout if nothing remains."""
        self.check()
        return min(timeout, self.remaining())
