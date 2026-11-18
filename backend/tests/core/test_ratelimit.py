from app.core.ratelimit import AttemptLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_at_limit_and_reports_retry_after() -> None:
    clock = Clock()
    lim = AttemptLimiter(limit=3, window_seconds=100, clock=clock)
    assert lim.retry_after("k") is None
    for _ in range(3):
        lim.record("k")
        clock.now += 10
    assert lim.retry_after("k") == 70  # oldest hit at t=1000, now 1030
    assert lim.retry_after("other") is None


def test_window_slides() -> None:
    clock = Clock()
    lim = AttemptLimiter(limit=2, window_seconds=100, clock=clock)
    lim.record("k")
    clock.now += 60
    lim.record("k")
    assert lim.retry_after("k") == 40
    clock.now += 41  # first hit expires
    assert lim.retry_after("k") is None


def test_reset_clears_key() -> None:
    lim = AttemptLimiter(limit=1)
    lim.record("k")
    assert lim.retry_after("k") is not None
    lim.reset("k")
    assert lim.retry_after("k") is None
