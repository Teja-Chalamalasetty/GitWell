"""RepoSnapshot: the only thing rules can see (PRD 8.2).

Rules never call GitHub, the database or the filesystem. A snapshot is fully built before any
rule runs, so rule order cannot change results.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from app.scanner.deadline import Deadline
from app.scanner.globbing import compile_glob

IGNORED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "dist",
        "build",
        "__pycache__",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "third_party",
        "site-packages",
    }
)
NUL_SNIFF_BYTES = 8192
DEFAULT_MAX_FILE_BYTES = 524288


@dataclass(frozen=True)
class FileEntry:
    path: str  # POSIX, relative to the repo root, no leading "/"
    size: int  # bytes


class RepoSnapshot(Protocol):
    full_name: str
    default_branch: str
    commit_sha: str
    mode: Literal["archive", "api"]
    is_partial: bool
    partial_reasons: list[str]
    max_file_bytes: int

    def files(self, include_ignored: bool = False) -> list[FileEntry]: ...
    def dirs(self, include_ignored: bool = False) -> set[str]: ...
    def root_file(self, names: Sequence[str]) -> FileEntry | None: ...
    def glob(self, pattern: str, include_ignored: bool = False) -> list[FileEntry]: ...
    def read_text(self, path: str) -> str | None: ...
    def check_deadline(self) -> None: ...


def _in_ignored_dir(path: str) -> bool:
    return any(part in IGNORED_DIRS for part in path.split("/")[:-1])


class BaseSnapshot:
    """In-memory snapshot used by both snapshot modes and by tests."""

    def __init__(
        self,
        *,
        entries: Iterable[FileEntry],
        contents: Mapping[str, bytes],
        full_name: str,
        default_branch: str,
        commit_sha: str,
        mode: Literal["archive", "api"],
        is_partial: bool = False,
        partial_reasons: Iterable[str] = (),
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        deadline: Deadline | None = None,
    ) -> None:
        by_path: dict[str, FileEntry] = {e.path: e for e in entries}
        self._all: list[FileEntry] = [by_path[p] for p in sorted(by_path)]
        self._visible: list[FileEntry] = [e for e in self._all if not _in_ignored_dir(e.path)]
        self._contents = contents
        self._deadline = deadline
        self.full_name = full_name
        self.default_branch = default_branch
        self.commit_sha = commit_sha
        self.mode: Literal["archive", "api"] = mode
        self.max_file_bytes = max_file_bytes
        reasons = sorted(set(partial_reasons))
        self.partial_reasons: list[str] = reasons
        self.is_partial = is_partial or bool(reasons)

    def check_deadline(self) -> None:
        if self._deadline is not None:
            self._deadline.check()

    def files(self, include_ignored: bool = False) -> list[FileEntry]:
        return list(self._all if include_ignored else self._visible)

    def dirs(self, include_ignored: bool = False) -> set[str]:
        found: set[str] = set()
        for entry in self._all if include_ignored else self._visible:
            parts = entry.path.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                found.add("/".join(parts[:i]))
        return found

    def root_file(self, names: Sequence[str]) -> FileEntry | None:
        order = {name.lower(): i for i, name in reversed(list(enumerate(names)))}
        best: tuple[int, str, FileEntry] | None = None
        for entry in self._all:
            if "/" in entry.path:
                continue
            idx = order.get(entry.path.lower())
            if idx is None:
                continue
            candidate = (idx, entry.path, entry)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        return best[2] if best else None

    def glob(self, pattern: str, include_ignored: bool = False) -> list[FileEntry]:
        regex = compile_glob(pattern)
        return [e for e in self.files(include_ignored) if regex.fullmatch(e.path)]

    def read_text(self, path: str) -> str | None:
        data = self._contents.get(path)
        if data is None or len(data) > self.max_file_bytes:
            return None
        if b"\x00" in data[:NUL_SNIFF_BYTES]:
            return None
        try:
            return data.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return None
