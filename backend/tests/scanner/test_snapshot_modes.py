"""API-mode snapshot, and the archive/API mode choice (PRD 8.2)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import respx

from app.github.client import GitHubClient
from app.github.errors import GitHubRepoTooLarge
from app.rules.base import Status
from app.rules.registry import get_rules
from app.scanner.api_snapshot import build_api_snapshot, prefetch_candidates
from app.scanner.archive_snapshot import SnapshotLimits
from app.scanner.deadline import Deadline
from app.scanner.engine import RepoInfo, build_snapshot, run_rules
from app.scanner.snapshot import FileEntry
from tests.fakes import make_tarball
from tests.github.conftest import BASE

SHA = "b" * 40
MB = 1024 * 1024
LIMITS = SnapshotLimits(50 * MB, 500 * MB, 100_000, 524_288, 200 * MB, 40)
REPO = RepoInfo("o", "r", "o/r", "main", size_kb=100)


def blob(path: str, size: int = 10) -> dict[str, object]:
    return {"path": path, "type": "blob", "size": size}


def tree_route(mock: respx.MockRouter, items: list[dict[str, object]], truncated: bool = False):  # type: ignore[no-untyped-def]
    return mock.get(f"{BASE}/repos/o/r/git/trees/{SHA}").respond(
        200, json={"tree": items, "truncated": truncated}
    )


def build_api(gh_client: GitHubClient, deadline: Deadline, limits: SnapshotLimits = LIMITS):  # type: ignore[no-untyped-def]
    return build_api_snapshot(
        gh_client,
        owner="o",
        name="r",
        full_name="o/r",
        default_branch="main",
        commit_sha=SHA,
        limits=limits,
        deadline=deadline,
    )


# ---- API mode ------------------------------------------------------------------------------


def test_api_snapshot_lists_blobs_and_prefetches_a_fixed_set(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(
        mock,
        [
            blob("README.md"),
            blob("LICENSE"),
            blob(".gitignore"),
            blob("package.json"),
            blob("CONTRIBUTING.md"),
            blob(".github/workflows/ci.yml"),
            blob("src/main.py", 999),
            {"path": "src", "type": "tree"},
            {"path": "sub", "type": "commit"},
        ],
    )
    contents = mock.get(url__regex=rf"{BASE}/repos/o/r/contents/.*").mock(
        side_effect=lambda req: respx.MockResponse(200, content=f"body of {req.url.path}".encode())
    )
    snap = build_api(gh_client, deadline)

    assert snap.mode == "api" and snap.is_partial
    assert "api_mode" in snap.partial_reasons
    assert [e.path for e in snap.files()] == [
        ".github/workflows/ci.yml",
        ".gitignore",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "package.json",
        "src/main.py",
    ]
    fetched = [c.request.url.path.removeprefix("/repos/o/r/contents/") for c in contents.calls]
    assert fetched == [
        "README.md",
        "LICENSE",
        ".gitignore",
        "package.json",
        "CONTRIBUTING.md",
        ".github/workflows/ci.yml",
    ]  # the PRD's priority order
    assert "body of" in (snap.read_text("README.md") or "")
    assert snap.read_text("src/main.py") is None  # never prefetched: no lazy fetching
    assert all(c.request.url.params["ref"] == SHA for c in contents.calls)


def test_truncated_tree_is_flagged(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(mock, [blob("a.py")], truncated=True)
    snap = build_api(gh_client, deadline)
    assert set(snap.partial_reasons) == {"api_mode", "tree_truncated"}


def test_prefetch_respects_the_fetch_cap_and_skips_oversized_files(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(
        mock,
        [
            blob("README.md", 10),
            blob("LICENSE", 10_000_000),
            blob(".gitignore"),
            blob("package.json"),
        ],
    )
    route = mock.get(url__regex=r".*/contents/.*").respond(200, content=b"x")
    snap = build_api(
        gh_client,
        deadline,
        SnapshotLimits(50 * MB, 500 * MB, 100_000, 524_288, 200 * MB, api_max_fetches=2),
    )
    assert route.call_count == 2  # README.md and .gitignore; LICENSE was too big to fetch
    assert "prefetch_limit" in snap.partial_reasons
    assert snap.read_text("README.md") == "x" and snap.read_text("package.json") is None


def test_a_missing_file_is_skipped_not_fatal(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(mock, [blob("README.md"), blob("LICENSE")])
    mock.get(f"{BASE}/repos/o/r/contents/README.md").respond(404)
    mock.get(f"{BASE}/repos/o/r/contents/LICENSE").respond(200, content=b"MIT")
    snap = build_api(gh_client, deadline)
    assert snap.read_text("README.md") is None and snap.read_text("LICENSE") == "MIT"


def test_prefetch_priority_groups() -> None:
    entries = [
        FileEntry(p, 5)
        for p in [
            "docs/CONTRIBUTING.md",
            ".github/CONTRIBUTING.md",
            "pkg/sub/deep/package.json",
            "a/requirements-dev.txt",
            "go.mod",
            ".github/workflows/z.yaml",
            ".github/workflows/a.yml",
            ".github/workflows/sub/x.yml",
            ".gitignore",
            "README.rst",
            "COPYING",
            "src/README.md",
        ]
    ]
    assert [e.path for e in prefetch_candidates(entries, 100)] == [
        "README.rst",
        "COPYING",  # group 1: README, then licence variants
        ".gitignore",  # group 2
        "a/requirements-dev.txt",
        "go.mod",  # group 3 (depth <= 2; deeper manifests excluded)
        ".github/CONTRIBUTING.md",
        "docs/CONTRIBUTING.md",  # group 4
        ".github/workflows/a.yml",
        ".github/workflows/z.yaml",  # group 5, direct children only
    ]


def test_rules_work_on_an_api_snapshot_with_the_documented_limits(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(
        mock,
        [
            blob("README.md", 400),
            blob("package.json", 60),
            blob(".env", 5),
            blob("package-lock.json", 100),
        ],
    )
    mock.get(f"{BASE}/repos/o/r/contents/README.md").respond(200, content=b"word " * 80)
    mock.get(f"{BASE}/repos/o/r/contents/package.json").respond(
        200, content=b'{"dependencies": {"a": "^1.0.0"}}'
    )
    snap = build_api(gh_client, deadline)
    by_id = {o.rule_id: o for o in run_rules(snap, get_rules(), deadline)}
    assert by_id["readme"].status is Status.PASS
    assert by_id["dependencies"].status is Status.PASS  # lock file exists (existence only)
    assert by_id["secrets"].status is Status.WARN
    assert by_id["secrets"].details["findings"][0]["pattern"] == "committed_env_file"
    assert by_id["secrets"].details["files_scanned"] == 0


# ---- choosing the mode ---------------------------------------------------------------------


ARCHIVE = make_tarball({"README.md": "# hi"})


def mock_archive(mock: respx.MockRouter, content: bytes = ARCHIVE) -> None:
    mock.get(f"{BASE}/repos/o/r/tarball/{SHA}").respond(
        302, headers={"Location": f"https://codeload.github.com/o/r/legacy.tar.gz/{SHA}"}
    )
    mock.get(url__startswith="https://codeload.github.com").respond(200, content=content)


def test_small_repos_use_archive_mode(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    mock_archive(mock)
    snap = build_snapshot(gh_client, REPO, SHA, LIMITS, deadline)
    assert snap.mode == "archive" and not snap.is_partial
    assert [e.path for e in snap.files()] == ["README.md"]


def test_repos_over_the_archive_limit_use_api_mode_when_a_token_exists(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    tree_route(mock, [blob("a.py")])
    big = RepoInfo("o", "r", "o/r", "main", size_kb=51 * 1024)
    snap = build_snapshot(gh_client, big, SHA, LIMITS, deadline)
    assert snap.mode == "api" and snap.is_partial
    assert not any("tarball" in str(c.request.url) for c in mock.calls)


def test_size_exactly_at_the_limit_still_uses_archive_mode(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    mock_archive(mock)
    at_limit = RepoInfo("o", "r", "o/r", "main", size_kb=50 * 1024)
    assert build_snapshot(gh_client, at_limit, SHA, LIMITS, deadline).mode == "archive"


def test_too_large_without_a_token_fails_with_repo_too_large(
    mock: respx.MockRouter, make_client: Callable[..., GitHubClient], deadline: Deadline
) -> None:
    anonymous = make_client(token=None)
    big = RepoInfo("o", "r", "o/r", "main", size_kb=51 * 1024)
    with pytest.raises(GitHubRepoTooLarge):
        build_snapshot(anonymous, big, SHA, LIMITS, deadline)
    assert len(mock.calls) == 0  # no API-mode calls were made


def test_archive_that_turns_out_too_big_falls_back_to_api_mode(
    mock: respx.MockRouter, gh_client: GitHubClient, deadline: Deadline
) -> None:
    mock_archive(mock, content=b"x" * 5000)  # larger than the (tiny) cap below
    tree_route(mock, [blob("a.py")])
    tiny = SnapshotLimits(1000, 500 * MB, 100_000, 524_288, 200 * MB, 40)
    snap = build_snapshot(gh_client, REPO, SHA, tiny, deadline)
    assert snap.mode == "api"


def test_archive_too_big_without_a_token_fails_with_repo_too_large(
    mock: respx.MockRouter, make_client: Callable[..., GitHubClient], deadline: Deadline
) -> None:
    mock_archive(mock, content=b"x" * 5000)
    tiny = SnapshotLimits(1000, 500 * MB, 100_000, 524_288, 200 * MB, 40)
    with pytest.raises(GitHubRepoTooLarge):
        build_snapshot(make_client(token=None), REPO, SHA, tiny, deadline)
