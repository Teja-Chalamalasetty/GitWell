import pytest

from app.rules.base import Status
from app.rules.gitignore import rule
from tests.fakes import FakeSnapshot, Unreadable


def test_fail_without_root_gitignore() -> None:
    result = rule.check(FakeSnapshot({"sub/.gitignore": "x", "README.md": "x"}))
    assert result.status is Status.FAIL
    assert result.details["excludes_env"] is False


def test_pass_for_repo_that_does_not_use_dotenv() -> None:
    result = rule.check(FakeSnapshot({".gitignore": "*.pyc\n"}))
    assert result.status is Status.PASS
    assert result.details == {"excludes_env": False, "uses_dotenv": False}


@pytest.mark.parametrize(
    "line",
    [".env", ".env*", ".env.*", "*.env", "/.env", "**/.env", "/.env*", "**/.env*", "  .env  "],
)
def test_pass_when_env_is_excluded(line: str) -> None:
    result = rule.check(
        FakeSnapshot({".gitignore": f"node_modules\n{line}\n", ".env.example": "A=1"})
    )
    assert result.status is Status.PASS
    assert result.details == {"excludes_env": True, "uses_dotenv": True}


def test_warn_when_dotenv_used_but_not_excluded() -> None:
    result = rule.check(FakeSnapshot({".gitignore": "*.pyc\n", ".env.example": "A=1"}))
    assert result.status is Status.WARN
    assert result.message == "`.gitignore` does not exclude `.env` files."
    assert result.details == {"excludes_env": False, "uses_dotenv": True}


@pytest.mark.parametrize("line", ["# .env", "!.env", ".env.local", "env", ".environment"])
def test_comments_negations_and_other_patterns_do_not_exclude(line: str) -> None:
    snap = FakeSnapshot({".gitignore": line + "\n", "config/.env": "A=1"})
    assert rule.check(snap).status is Status.WARN


def test_env_file_at_any_depth_means_dotenv_is_used() -> None:
    snap = FakeSnapshot({".gitignore": "*.pyc", "services/api/.env.production": "A=1"})
    assert rule.check(snap).details["uses_dotenv"] is True


def test_dotenv_dependency_in_a_manifest_means_dotenv_is_used() -> None:
    for manifest, text in (
        ("requirements.txt", "python-dotenv==1.0\n"),
        ("package.json", '{"dependencies": {"dotenv": "16.0.0"}}'),
        ("pyproject.toml", '[project]\ndependencies = ["python-dotenv"]\n'),
    ):
        result = rule.check(FakeSnapshot({".gitignore": "*.pyc", manifest: text}))
        assert result.status is Status.WARN, manifest


def test_env_files_inside_ignored_directories_are_ignored() -> None:
    snap = FakeSnapshot({".gitignore": "*.pyc", "node_modules/pkg/.env": "A=1"})
    assert rule.check(snap).status is Status.PASS


def test_unreadable_gitignore_gets_the_benefit_of_the_doubt() -> None:
    snap = FakeSnapshot({".gitignore": Unreadable(9_999_999), ".env.example": "A=1"})
    assert rule.check(snap).status is Status.PASS


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("gitignore", 10, 70)
