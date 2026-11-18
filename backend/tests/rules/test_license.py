import pytest

from app.rules.base import Status
from app.rules.license import rule
from tests.fakes import FakeSnapshot, Unreadable


@pytest.mark.parametrize(
    "name",
    [
        "LICENSE",
        "LICENSE.md",
        "LICENSE.txt",
        "license",
        "Licence.md",
        "COPYING",
        "UNLICENSE",
        "copying.txt",
    ],
)
def test_pass_for_known_names(name: str) -> None:
    result = rule.check(FakeSnapshot({name: "MIT License ..."}))
    assert result.status is Status.PASS
    assert result.details == {"path": name}


def test_fail_when_missing() -> None:
    assert rule.check(FakeSnapshot({"README.md": "x"})).status is Status.FAIL


@pytest.mark.parametrize("name", ["docs/LICENSE", "LICENSE-MIT", "license_notes.txt"])
def test_only_exact_root_names_count(name: str) -> None:
    assert rule.check(FakeSnapshot({name: "MIT"})).status is Status.FAIL


def test_warn_when_found_but_empty() -> None:
    assert rule.check(FakeSnapshot({"LICENSE": ""})).status is Status.WARN
    assert rule.check(FakeSnapshot({"LICENSE": "  \n"})).status is Status.WARN


def test_unreadable_non_empty_license_passes() -> None:
    assert rule.check(FakeSnapshot({"LICENSE": Unreadable(9000)})).status is Status.PASS


def test_pass_if_any_candidate_is_non_empty() -> None:
    result = rule.check(FakeSnapshot({"LICENSE": "", "COPYING": "GPL text"}))
    assert result.status is Status.PASS and result.details["path"] == "COPYING"


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("license", 10, 50)
