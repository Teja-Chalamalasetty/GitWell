import pytest

from app.rules.base import Status
from app.rules.tests import rule
from tests.fakes import FakeSnapshot


def run(*paths: str) -> object:
    return rule.check(FakeSnapshot({p: "x" for p in paths}))


@pytest.mark.parametrize(
    "path",
    [
        "test_api.py",
        "pkg/test_api.py",
        "api_test.py",
        "a.test.js",
        "a.test.jsx",
        "a.test.ts",
        "a.test.tsx",
        "a.test.mjs",
        "a.test.cjs",
        "a.spec.js",
        "a.spec.jsx",
        "a.spec.ts",
        "a.spec.tsx",
        "user_test.go",
        "user_spec.rb",
        "FooTest.java",
        "FooTests.java",
        "FooTest.kt",
        "FooTests.cs",
        "TEST_API.PY",  # the first group is case-insensitive
    ],
)
def test_pass_for_each_test_file_pattern(path: str) -> None:
    result = rule.check(FakeSnapshot({path: "x"}))
    assert result.status is Status.PASS, path
    assert result.details["test_file_count"] == 1


@pytest.mark.parametrize("path", ["footest.java", "FOOTEST.JAVA", "Footests.cs"])
def test_java_kotlin_csharp_patterns_are_case_sensitive(path: str) -> None:
    assert rule.check(FakeSnapshot({path: "x"})).status is Status.FAIL


def test_pass_for_any_source_file_under_a_test_directory() -> None:
    """unittest-style layouts such as tests/api.py"""
    for path in ("tests/api.py", "src/__tests__/helpers.ts", "spec/models/user.rb", "a/Test/x.go"):
        assert rule.check(FakeSnapshot({path: "x"})).status is Status.PASS, path


def test_warn_when_test_dir_has_only_non_source_files() -> None:
    result = rule.check(FakeSnapshot({"tests/fixtures/data.json": "{}", "tests/.gitkeep": ""}))
    assert result.status is Status.WARN
    assert result.details == {"test_file_count": 0, "sample": []}


def test_fail_when_nothing_matches() -> None:
    result = rule.check(FakeSnapshot({"src/main.py": "x", "README.md": "x", "latest.py": "x"}))
    assert result.status is Status.FAIL
    assert "No test files" in result.message


def test_names_that_merely_contain_test_do_not_count() -> None:
    assert rule.check(FakeSnapshot({"contest.py": "x", "attest.js": "x"})).status is Status.FAIL


def test_ignored_directories_are_not_searched() -> None:
    snap = FakeSnapshot({"node_modules/pkg/test_x.py": "x", "vendor/tests/a.go": "x"})
    assert rule.check(snap).status is Status.FAIL


def test_sample_is_sorted_and_capped_at_five() -> None:
    paths = {f"tests/test_{i}.py": "x" for i in range(9, -1, -1)}
    result = rule.check(FakeSnapshot(paths))
    assert result.details["test_file_count"] == 10
    assert result.details["sample"] == [f"tests/test_{i}.py" for i in range(5)]


def test_source_extension_must_be_in_the_list() -> None:
    assert rule.check(FakeSnapshot({"tests/notes.md": "x", "tests/data.csv": "x"})).status is (
        Status.WARN
    )
    assert rule.check(FakeSnapshot({"tests/lib.swift": "x"})).status is Status.PASS
    assert rule.check(FakeSnapshot({"tests/lib.PHP": "x"})).status is Status.PASS


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("tests", 15, 30)
