"""Helpers shared by rules. Underscore-prefixed, so never treated as a rule module."""

from __future__ import annotations

from app.scanner.snapshot import FileEntry, RepoSnapshot


def is_empty(snap: RepoSnapshot, entry: FileEntry) -> bool:
    """PRD 9 emptiness rule.

    Empty = size 0, or readable text that is blank after strip(). Unreadable content with
    size > 0 (too large, binary, not UTF-8, not fetched) counts as NON-empty.
    """
    if entry.size == 0:
        return True
    text = snap.read_text(entry.path)
    return text is not None and text.strip() == ""


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def root_files(snap: RepoSnapshot) -> list[FileEntry]:
    return [e for e in snap.files() if "/" not in e.path]
