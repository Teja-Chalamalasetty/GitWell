"""Dogfooding (PRD 16, 17 M7): GitWell must score 100 when scanned by GitWell.

The file list is what `git ls-files` reports. Outside a git checkout (a fresh working copy that has
not been committed yet) it falls back to walking the tree and honouring `.gitignore`, which is what
`git add .` would pick up.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pathspec

from app.rules.registry import get_rules
from app.scanner.deadline import Deadline
from app.scanner.engine import run_rules
from app.scanner.scoring import compute_score
from tests.fakes import FakeSnapshot, Unreadable

ROOT = Path(__file__).resolve().parents[2]
MAX_READ_BYTES = 2 * 1024 * 1024  # bigger files are listed but not read (the scan would skip them)


def git_files(root: Path) -> list[str] | None:
    """Tracked files according to git, or None if `root` is not a git checkout."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True, timeout=60
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return sorted(p for p in result.stdout.decode().split("\0") if p)


def walked_files(root: Path) -> list[str]:
    """Every file `git add .` would add: everything not ignored by the root `.gitignore`."""
    ignore_file = root / ".gitignore"
    spec = pathspec.GitIgnoreSpec.from_lines(
        ignore_file.read_text(encoding="utf-8").splitlines() if ignore_file.exists() else []
    )
    found: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        for entry in directory.iterdir():
            relative = entry.relative_to(root).as_posix()
            if entry.name == ".git":
                continue
            if entry.is_dir():
                if not spec.match_file(relative + "/"):
                    stack.append(entry)
            elif not spec.match_file(relative):
                found.append(relative)
    return sorted(found)


def repo_files(root: Path) -> list[str]:
    listed = git_files(root)
    return listed if listed else walked_files(root)


def build_snapshot(root: Path, paths: list[str]) -> FakeSnapshot:
    files: dict[str, bytes | Unreadable] = {}
    for path in paths:
        full = root / path
        if not full.is_file():  # a tracked file that was deleted in the working tree
            continue
        size = full.stat().st_size
        files[path] = full.read_bytes() if size <= MAX_READ_BYTES else Unreadable(size)
    return FakeSnapshot(files, full_name="gitwell/gitwell", default_branch="main")


def describe(outcomes: list) -> str:  # type: ignore[type-arg]
    lines = []
    for o in outcomes:
        if o.status.value != "pass":
            where = [
                f"{f['path']}:{f['line']} ({f['pattern']})" for f in o.details.get("findings", [])
            ]
            lines.append(f"  {o.rule_id}: {o.status.value}: {o.message} {where}")
    return "\n".join(lines)


# ---- the self-scan ----------------------------------------------------------------------------


def test_gitwell_scores_100_when_scanned_by_gitwell() -> None:
    paths = repo_files(ROOT)
    snapshot = build_snapshot(ROOT, paths)
    outcomes = run_rules(snapshot, get_rules(), Deadline(120))
    result = compute_score([o.scored() for o in outcomes])
    assert result.score == 100, (
        f"GitWell scored {result.score}; failing rules:\n{describe(outcomes)}"
    )
    assert result.rules_errored == 0
    assert all(o.status.value in ("pass", "not_applicable") for o in outcomes), describe(outcomes)


def test_the_self_scan_really_sees_the_repository() -> None:
    """Guards against a vacuous pass: the file list must contain what we expect it to."""
    paths = set(repo_files(ROOT))
    for expected in (
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        ".gitignore",
        ".env.example",
        ".github/workflows/ci.yml",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "frontend/package.json",
        "frontend/package-lock.json",
        "docs/RULES.md",
    ):
        assert expected in paths, f"{expected} missing from the scanned file list"
    assert len(paths) > 100
    # ignored and generated content is never scanned (or committed)
    assert not any(
        p.startswith(("backend/.venv/", "frontend/node_modules/", ".git/")) for p in paths
    )
    assert not any(p.endswith((".db", ".pyc")) for p in paths)


def test_no_secret_shaped_literal_exists_anywhere_in_the_repository() -> None:
    """The secrets rule must find nothing, including medium and low confidence findings."""
    snapshot = build_snapshot(ROOT, repo_files(ROOT))
    (secrets,) = (r for r in get_rules() if r.id == "secrets")
    result = secrets.check(snapshot)
    assert result.details["findings"] == [], describe(
        [
            type(
                "O",
                (),
                {
                    "rule_id": "secrets",
                    "status": result.status,
                    "message": result.message,
                    "details": result.details,
                },
            )()
        ]
    )
    assert result.status.value == "pass"


# ---- the file-listing helpers themselves --------------------------------------------------------


def test_the_fallback_walker_honours_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(
        ".env\n.env.*\n!.env.example\n*.db\nnode_modules/\ndist/\n"
    )
    for name in (
        ".env",
        ".env.local",
        ".env.example",
        "a.db",
        "keep.py",
        "sub/keep.txt",
        "node_modules/x/y.js",
        "dist/out.js",
        "sub/dist/z.js",
        ".git/config",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert walked_files(tmp_path) == [".env.example", ".gitignore", "keep.py", "sub/keep.txt"]


def test_git_ls_files_is_used_inside_a_checkout(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "tracked.py").write_text("x")
    (tmp_path / "untracked.py").write_text("x")
    git("add", "tracked.py")
    assert git_files(tmp_path) == ["tracked.py"]  # git's answer, not the walker's
    assert repo_files(tmp_path) == ["tracked.py"]


def test_outside_a_checkout_there_is_no_git_answer(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x")
    assert git_files(tmp_path) is None
    assert repo_files(tmp_path) == ["a.py"]
