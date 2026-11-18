"""API mode: fallback for repositories too large to download as an archive (PRD 8.2).

The file list comes from the trees API. Before any rule runs, a fixed, prioritised set of files is
prefetched; nothing is fetched lazily, so rule order cannot change results. The snapshot is always
partial in this mode.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from app.github.client import GitHubClient
from app.github.errors import GitHubError, GitHubNotFound
from app.scanner.archive_snapshot import SnapshotLimits
from app.scanner.deadline import Deadline
from app.scanner.snapshot import BaseSnapshot, FileEntry

logger = logging.getLogger("gitwell.scanner")

# README first, then license variants, in the PRD's order (matters when the fetch cap bites).
_ROOT_DOC_ORDER = {"readme": 0, "license": 1, "licence": 1, "copying": 2, "unlicense": 3}
_MANIFEST_NAMES = {"pyproject.toml", "package.json", "cargo.toml", "go.mod", "gemfile", "pipfile"}
_REQUIREMENTS_RE = re.compile(r"^requirements.*\.txt$", re.IGNORECASE)
_CONTRIBUTING_NAMES = {"contributing", "contributing.md", "contributing.rst"}


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_readme_or_license(path: str) -> bool:
    if "/" in path:
        return False
    name = path.lower()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem in _ROOT_DOC_ORDER


def _root_doc_rank(path: str) -> int:
    name = path.lower()
    return _ROOT_DOC_ORDER.get(name.rsplit(".", 1)[0] if "." in name else name, 99)


def _is_manifest(path: str) -> bool:
    if path.count("/") > 2:
        return False
    name = _basename(path).lower()
    return name in _MANIFEST_NAMES or bool(_REQUIREMENTS_RE.match(name))


def _is_contributing(path: str) -> bool:
    parent, _, name = path.rpartition("/")
    return parent.lower() in ("", ".github", "docs") and name.lower() in _CONTRIBUTING_NAMES


def _is_workflow(path: str) -> bool:
    parent, _, name = path.rpartition("/")
    return parent == ".github/workflows" and name.lower().endswith((".yml", ".yaml"))


# Priority order from the PRD.
_PRIORITY: tuple[Callable[[str], bool], ...] = (
    _is_readme_or_license,
    lambda p: p == ".gitignore",
    _is_manifest,
    _is_contributing,
    _is_workflow,
)


def prefetch_candidates(entries: list[FileEntry], max_file_bytes: int) -> list[FileEntry]:
    """Files to fetch, in priority order (each group sorted by path), without size-skipped ones."""
    chosen: list[FileEntry] = []
    seen: set[str] = set()
    for matches in _PRIORITY:
        for entry in sorted(entries, key=lambda e: (_root_doc_rank(e.path), e.path)):
            if entry.path in seen or not matches(entry.path) or entry.size > max_file_bytes:
                continue
            seen.add(entry.path)
            chosen.append(entry)
    return chosen


def build_api_snapshot(
    client: GitHubClient,
    *,
    owner: str,
    name: str,
    full_name: str,
    default_branch: str,
    commit_sha: str,
    limits: SnapshotLimits,
    deadline: Deadline,
) -> BaseSnapshot:
    reasons = {"api_mode"}
    tree = client.get_tree(owner, name, commit_sha, deadline)
    raw_tree = tree.get("tree")
    if not isinstance(raw_tree, list):
        raise GitHubError({"reason": "unexpected_tree_shape"})
    if tree.get("truncated") is True:
        reasons.add("tree_truncated")

    entries: list[FileEntry] = []
    for item in raw_tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        path, size = item.get("path"), item.get("size", 0)
        if isinstance(path, str) and isinstance(size, int):
            entries.append(FileEntry(path=path, size=size))

    candidates = prefetch_candidates(entries, limits.max_file_bytes)
    if len(candidates) > limits.api_max_fetches:
        reasons.add("prefetch_limit")
    contents: dict[str, bytes] = {}
    for entry in candidates[: limits.api_max_fetches]:
        deadline.check()
        try:
            contents[entry.path] = client.get_file_raw(
                owner, name, entry.path, commit_sha, deadline
            )
        except GitHubNotFound:
            logger.info("api_snapshot_file_missing", extra={"owner": owner, "repo": name})

    return BaseSnapshot(
        entries=entries,
        contents=contents,
        full_name=full_name,
        default_branch=default_branch,
        commit_sha=commit_sha,
        mode="api",
        is_partial=True,
        partial_reasons=reasons,
        max_file_bytes=limits.max_file_bytes,
        deadline=deadline,
    )
