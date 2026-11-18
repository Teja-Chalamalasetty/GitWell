import pytest

from app.scanner.deadline import Deadline, ScanTimeout


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_check_passes_until_the_deadline_then_raises() -> None:
    clock = Clock()
    d = Deadline(10, clock)
    d.check()
    clock.now += 9.9
    d.check()
    clock.now += 0.2
    with pytest.raises(ScanTimeout):
        d.check()
    assert d.expired()


def test_clamp_limits_a_timeout_to_what_remains() -> None:
    clock = Clock()
    d = Deadline(10, clock)
    assert d.clamp(5) == 5
    clock.now += 8
    assert d.clamp(5) == pytest.approx(2)
    clock.now += 5
    with pytest.raises(ScanTimeout):
        d.clamp(5)


def test_expire_makes_every_later_check_fail() -> None:
    d = Deadline.none()
    d.check()
    assert d.clamp(7) == 7
    d.expire()
    with pytest.raises(ScanTimeout):
        d.check()
