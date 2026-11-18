"""Archive mode: one tarball download, processed entirely in memory (PRD 8.2, S9).

Nothing is extracted to disk and nothing from the archive is ever executed. Only regular files
are accepted; paths are normalised; compressed size, uncompressed size, member count and the text
budget are all capped, so a hostile archive cannot exhaust memory, disk or time.
"""

from __future__ import annotations

import tarfile
import tempfile
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.config import Settings
from app.github.errors import GitHubError
from app.scanner.deadline import Deadline
from app.scanner.snapshot import BaseSnapshot, FileEntry

SPOOL_BYTES = 8 * 1024 * 1024
MB = 1024 * 1024


class ArchiveTooLarge(Exception):
    """The compressed download exceeded MAX_ARCHIVE_MB; the caller falls back to API mode."""


@dataclass(frozen=True)
class SnapshotLimits:
    max_archive_bytes: int
    max_uncompressed_bytes: int
    max_files: int
    max_file_bytes: int
    max_text_total_bytes: int
    api_max_fetches: int

    @classmethod
    def from_settings(cls, s: Settings) -> SnapshotLimits:
        return cls(
            max_archive_bytes=s.max_archive_mb * MB,
            max_uncompressed_bytes=s.max_uncompressed_mb * MB,
            max_files=s.max_files,
            max_file_bytes=s.max_file_bytes,
            max_text_total_bytes=s.max_text_total_mb * MB,
            api_max_fetches=s.api_snapshot_max_fetches,
        )


class ArchiveSnapshot(BaseSnapshot):
    skipped_links: int
    skipped_unsafe: int


def safe_member_path(name: str) -> str | None:
    """Normalise a tar member name, dropping the first component (the repo's root folder).

    Returns None for absolute names, names containing "..", or names with nothing left.
    """
    if not name or "\x00" in name:
        return None
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    parts = pure.parts[1:]
    if not parts:
        return None
    return "/".join(parts)


def build_archive_snapshot(
    chunks: Iterable[bytes],
    *,
    full_name: str,
    default_branch: str,
    commit_sha: str,
    limits: SnapshotLimits,
    deadline: Deadline,
) -> ArchiveSnapshot:
    iterator = iter(chunks)
    try:
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES) as spool:
            downloaded = 0
            for chunk in iterator:
                deadline.check()
                downloaded += len(chunk)
                if downloaded > limits.max_archive_bytes:
                    raise ArchiveTooLarge
                spool.write(chunk)
            spool.seek(0)
            return _read_archive(
                spool,
                full_name=full_name,
                default_branch=default_branch,
                commit_sha=commit_sha,
                limits=limits,
                deadline=deadline,
            )
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()


def _read_archive(
    spool: tempfile.SpooledTemporaryFile[bytes],
    *,
    full_name: str,
    default_branch: str,
    commit_sha: str,
    limits: SnapshotLimits,
    deadline: Deadline,
) -> ArchiveSnapshot:
    entries: dict[str, FileEntry] = {}
    contents: dict[str, bytes] = {}
    reasons: set[str] = set()
    skipped_links = 0
    skipped_unsafe = 0
    kept_bytes = 0
    member_count = 0
    declared_bytes = 0

    try:
        tar = tarfile.open(fileobj=spool, mode="r:gz")  # noqa: SIM115 - closed in finally
    except (tarfile.TarError, EOFError, zlib.error, OSError):
        raise GitHubError({"reason": "invalid_archive"}) from None

    try:
        while True:
            member = tar.next()
            tar.members = []  # type: ignore[attr-defined]  # tarfile caches every member; do not keep 100k of them
            if member is None:
                break
            member_count += 1
            if member_count > limits.max_files:
                reasons.add("archive_limits")
                break
            if member_count % 100 == 0:
                deadline.check()
            # Checked from the header alone, before the data is skipped, so a decompression
            # bomb is never decompressed past the cap.
            if declared_bytes + member.size > limits.max_uncompressed_bytes:
                reasons.add("archive_limits")
                break
            declared_bytes += member.size

            if member.isdir():
                continue
            if not member.isfile():
                skipped_links += 1  # symlinks, hardlinks, devices, fifos
                continue
            path = safe_member_path(member.name)
            if path is None:
                skipped_unsafe += 1
                continue

            old = contents.pop(path, None)
            if old is not None:
                kept_bytes -= len(old)
            entries[path] = FileEntry(path=path, size=member.size)

            if member.size > limits.max_file_bytes:
                continue
            if kept_bytes + member.size > limits.max_text_total_bytes:
                reasons.add("text_budget_exceeded")
                continue
            fileobj = tar.extractfile(member)
            if fileobj is None:
                continue
            data = fileobj.read(limits.max_file_bytes + 1)
            contents[path] = data
            kept_bytes += len(data)
    except (tarfile.TarError, EOFError, zlib.error, OSError):
        raise GitHubError({"reason": "invalid_archive"}) from None
    finally:
        tar.close()

    snapshot = ArchiveSnapshot(
        entries=entries.values(),
        contents=contents,
        full_name=full_name,
        default_branch=default_branch,
        commit_sha=commit_sha,
        mode="archive",
        is_partial=bool(reasons),
        partial_reasons=reasons,
        max_file_bytes=limits.max_file_bytes,
        deadline=deadline,
    )
    snapshot.skipped_links = skipped_links
    snapshot.skipped_unsafe = skipped_unsafe
    return snapshot
