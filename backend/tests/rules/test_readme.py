from app.rules.base import Status
from app.rules.readme import rule
from tests.fakes import FakeSnapshot, Unreadable

LONG = "word " * 60  # 240 non-whitespace characters


def test_fail_when_missing() -> None:
    result = rule.check(FakeSnapshot({"src/main.py": "x"}))
    assert result.status is Status.FAIL


def test_readme_must_be_at_root() -> None:
    assert rule.check(FakeSnapshot({"docs/README.md": LONG})).status is Status.FAIL


def test_fail_when_empty_or_whitespace() -> None:
    assert rule.check(FakeSnapshot({"README.md": ""})).status is Status.FAIL
    assert rule.check(FakeSnapshot({"README.md": " \n\t\n "})).status is Status.FAIL


def test_warn_when_very_short() -> None:
    result = rule.check(FakeSnapshot({"README.md": "# Tiny project\n\nDoes a thing."}))
    assert result.status is Status.WARN
    assert result.message == "README is very short (23 characters)."
    assert result.details == {"path": "README.md", "length": 23}


def test_boundary_at_150_non_whitespace_characters() -> None:
    assert rule.check(FakeSnapshot({"README.md": "a" * 149})).status is Status.WARN
    assert rule.check(FakeSnapshot({"README.md": "a" * 150})).status is Status.PASS


def test_whitespace_does_not_count_toward_length() -> None:
    assert rule.check(FakeSnapshot({"README.md": "a b " * 40})).details["length"] == 80


def test_pass_with_details() -> None:
    result = rule.check(FakeSnapshot({"README.md": LONG}))
    assert result.status is Status.PASS
    assert result.details == {"path": "README.md", "length": 240}


def test_case_insensitive_name_and_all_variants() -> None:
    for name in ("readme.md", "README", "Readme.RST", "README.txt", "README.markdown"):
        assert rule.check(FakeSnapshot({name: LONG})).status is Status.PASS, name


def test_unreadable_content_with_size_counts_as_non_empty_and_passes() -> None:
    result = rule.check(FakeSnapshot({"README.md": Unreadable(5000)}))
    assert result.status is Status.PASS
    assert result.details == {"path": "README.md", "length": 5000}


def test_zero_byte_unreadable_is_empty() -> None:
    assert rule.check(FakeSnapshot({"README.md": Unreadable(0)})).status is Status.FAIL


def test_preferred_file_order() -> None:
    snap = FakeSnapshot({"README": LONG, "README.md": "tiny"})
    assert rule.check(snap).details["path"] == "README.md"


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("readme", 15, 20)
    assert "README" in rule.fix_tip
