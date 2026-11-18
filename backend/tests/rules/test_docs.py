import pytest

from app.rules.base import Status
from app.rules.docs import rule
from tests.fakes import FakeSnapshot


@pytest.mark.parametrize(
    "path",
    [
        "CONTRIBUTING",
        "CONTRIBUTING.md",
        "CONTRIBUTING.rst",
        "contributing.md",
        ".github/CONTRIBUTING.md",
        "docs/CONTRIBUTING.rst",
        "Docs/Contributing.MD",
    ],
)
def test_pass_with_contributing_file(path: str) -> None:
    result = rule.check(FakeSnapshot({path: "How to contribute"}))
    assert result.status is Status.PASS
    assert result.details["contributing"] == path


def test_pass_when_root_docs_directory_has_a_file() -> None:
    result = rule.check(FakeSnapshot({"docs/index.md": "x"}))
    assert result.status is Status.PASS
    assert result.details == {"contributing": None, "has_docs_dir": True}


def test_nested_docs_directory_does_not_count() -> None:
    assert rule.check(FakeSnapshot({"src/docs/a.md": "x"})).status is Status.FAIL


def test_contributing_in_other_folders_does_not_count() -> None:
    assert rule.check(FakeSnapshot({"src/CONTRIBUTING.md": "x"})).status is Status.FAIL


def test_unsupported_contributing_extension_does_not_count() -> None:
    assert rule.check(FakeSnapshot({"CONTRIBUTING.txt": "x"})).status is Status.FAIL


def test_fail_when_neither() -> None:
    result = rule.check(FakeSnapshot({"README.md": "x", "src/a.py": "x"}))
    assert result.status is Status.FAIL
    assert result.details == {"contributing": None, "has_docs_dir": False}


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("docs", 5, 80)
