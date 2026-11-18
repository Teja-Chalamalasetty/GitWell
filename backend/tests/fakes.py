"""Test doubles: FakeSnapshot, an exploding snapshot, and in-memory tarball builders."""

from __future__ import annotations

import gzip
import io
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.scanner.snapshot import DEFAULT_MAX_FILE_BYTES, BaseSnapshot, FileEntry


@dataclass(frozen=True)
class Unreadable:
    """A file whose size is known but whose text cannot be read (too large, binary, unfetched)."""

    size: int = 10


FileSpec = str | bytes | Unreadable


class FakeSnapshot(BaseSnapshot):
    """`FakeSnapshot(files={path: content})` using the real snapshot semantics."""

    def __init__(
        self,
        files: Mapping[str, FileSpec] | None = None,
        *,
        mode: Literal["archive", "api"] = "archive",
        is_partial: bool = False,
        partial_reasons: tuple[str, ...] = (),
        commit_sha: str = "abcdef1234567890abcdef1234567890abcdef12",
        default_branch: str = "main",
        full_name: str = "o/r",
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        entries: list[FileEntry] = []
        contents: dict[str, bytes] = {}
        for path, spec in (files or {}).items():
            if isinstance(spec, Unreadable):
                entries.append(FileEntry(path, spec.size))
                continue
            data = spec.encode() if isinstance(spec, str) else spec
            entries.append(FileEntry(path, len(data)))
            contents[path] = data
        super().__init__(
            entries=entries,
            contents=contents,
            full_name=full_name,
            default_branch=default_branch,
            commit_sha=commit_sha,
            mode=mode,
            is_partial=is_partial,
            partial_reasons=partial_reasons,
            max_file_bytes=max_file_bytes,
        )


class ExplodingSnapshot(FakeSnapshot):
    """Every read raises, to prove a crashing rule becomes an `error` result."""

    def _boom(self) -> Any:
        raise RuntimeError("boom: contains-secret-looking-text")

    def files(self, include_ignored: bool = False) -> list[FileEntry]:
        return self._boom()  # type: ignore[no-any-return]

    def dirs(self, include_ignored: bool = False) -> set[str]:
        return self._boom()  # type: ignore[no-any-return]

    def root_file(self, names: Any) -> FileEntry | None:
        return self._boom()  # type: ignore[no-any-return]

    def glob(self, pattern: str, include_ignored: bool = False) -> list[FileEntry]:
        return self._boom()  # type: ignore[no-any-return]

    def read_text(self, path: str) -> str | None:
        return self._boom()  # type: ignore[no-any-return]


# ---- tarballs ------------------------------------------------------------------------------

ROOT = "owner-repo-abc1234"


def _tar_to_gz(build: Callable[[tarfile.TarFile], None]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        build(tar)
    return raw.getvalue()


def add_file(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def make_tarball(
    files: Mapping[str, str | bytes],
    *,
    root: str = ROOT,
    extra: Callable[[tarfile.TarFile], None] | None = None,
) -> bytes:
    """A GitHub-style tarball: every path lives under a single top-level folder."""

    def build(tar: tarfile.TarFile) -> None:
        folder = tarfile.TarInfo(root)
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)
        for path, content in files.items():
            add_file(
                tar, f"{root}/{path}", content.encode() if isinstance(content, str) else content
            )
        if extra:
            extra(tar)

    return _tar_to_gz(build)


def chunked(data: bytes, size: int = 4096) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def make_zero_bomb_tarball(size: int, *, root: str = ROOT) -> bytes:
    """A tiny gzip that would expand to `size` bytes of zeros inside one tar member."""
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as gz:
        small = tarfile.TarInfo(f"{root}/README.md")
        small.size = 5
        gz.write(small.tobuf(tarfile.GNU_FORMAT) + b"hello" + b"\0" * 507)
        big = tarfile.TarInfo(f"{root}/zeros.bin")
        big.size = size
        gz.write(big.tobuf(tarfile.GNU_FORMAT))
        block = b"\0" * (1024 * 1024)
        remaining = size
        while remaining > 0:
            n = min(len(block), remaining)
            gz.write(block[:n])
            remaining -= n
        gz.write(b"\0" * ((-size) % 512) + b"\0" * 1024)
    return raw.getvalue()


def make_many_members_tarball(count: int, *, root: str = ROOT) -> bytes:
    """`count` tiny empty files (a member-count bomb)."""
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as gz:
        for i in range(count):
            info = tarfile.TarInfo(f"{root}/d{i % 100}/f{i}.txt")
            gz.write(info.tobuf(tarfile.GNU_FORMAT))
        gz.write(b"\0" * 1024)
    return raw.getvalue()
