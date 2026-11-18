import pytest

from app.rules.base import Status
from app.rules.ci import rule
from tests.fakes import FakeSnapshot, Unreadable

WORKFLOW = "name: CI\non: push\n"


def test_pass_with_github_actions() -> None:
    result = rule.check(FakeSnapshot({".github/workflows/ci.yml": WORKFLOW}))
    assert result.status is Status.PASS
    assert result.details == {
        "providers": ["github_actions"],
        "files": [".github/workflows/ci.yml"],
    }


def test_yaml_extension_and_case_insensitive() -> None:
    assert rule.check(FakeSnapshot({".github/workflows/build.YAML": WORKFLOW})).status is (
        Status.PASS
    )


def test_workflows_in_subfolders_do_not_count() -> None:
    snap = FakeSnapshot({".github/workflows/sub/ci.yml": WORKFLOW})
    assert rule.check(snap).status is Status.FAIL


@pytest.mark.parametrize(
    ("path", "provider"),
    [
        (".gitlab-ci.yml", "gitlab_ci"),
        (".circleci/config.yml", "circleci"),
        ("Jenkinsfile", "jenkins"),
        ("azure-pipelines.yml", "azure_pipelines"),
        (".travis.yml", "travis_ci"),
        ("bitbucket-pipelines.yml", "bitbucket_pipelines"),
    ],
)
def test_other_providers(path: str, provider: str) -> None:
    result = rule.check(FakeSnapshot({path: "pipeline: x"}))
    assert result.status is Status.PASS
    assert result.details["providers"] == [provider]


def test_other_providers_must_be_at_root() -> None:
    assert rule.check(FakeSnapshot({"sub/Jenkinsfile": "x"})).status is Status.FAIL


def test_fail_when_nothing() -> None:
    result = rule.check(FakeSnapshot({"README.md": "x"}))
    assert result.status is Status.FAIL
    assert result.details == {"providers": [], "files": []}


def test_warn_when_all_ci_files_are_empty() -> None:
    snap = FakeSnapshot({".github/workflows/ci.yml": "", "Jenkinsfile": "  \n"})
    result = rule.check(snap)
    assert result.status is Status.WARN
    assert result.details["providers"] == ["github_actions", "jenkins"]


def test_pass_if_at_least_one_is_non_empty() -> None:
    snap = FakeSnapshot({".github/workflows/a.yml": "", ".github/workflows/b.yml": WORKFLOW})
    assert rule.check(snap).status is Status.PASS


def test_unreadable_non_empty_file_counts_as_non_empty() -> None:
    assert rule.check(FakeSnapshot({"Jenkinsfile": Unreadable(5000)})).status is Status.PASS


def test_multiple_providers_are_listed_in_stable_order() -> None:
    snap = FakeSnapshot({".travis.yml": "x", ".github/workflows/z.yml": "x", ".gitlab-ci.yml": "x"})
    assert rule.check(snap).details["providers"] == ["github_actions", "gitlab_ci", "travis_ci"]


def test_fix_tip_contains_a_minimal_example() -> None:
    assert "```yaml" in rule.fix_tip
    block = rule.fix_tip.split("```yaml")[1].split("```")[0].strip().splitlines()
    assert 10 <= len(block) <= 16


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("ci", 15, 40)
