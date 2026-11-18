import io
import tarfile
import tracemalloc

import pytest

from app.github.errors import GitHubError
from app.scanner.archive_snapshot import (
    ArchiveTooLarge,
    SnapshotLimits,
    build_archive_snapshot,
    safe_member_path,
)
from app.scanner.deadline import Deadline, ScanTimeout
from tests.fakes import (
    ROOT,
    add_file,
    chunked,
    make_many_members_tarball,
    make_tarball,
    make_zero_bomb_tarball,
)

MB = 1024 * 1024


def limits(**overrides: int) -> SnapshotLimits:
    values = {
        "max_archive_bytes": 50 * MB,
        "max_uncompressed_bytes": 500 * MB,
        "max_files": 100_000,
        "max_file_bytes": 524_288,
        "max_text_total_bytes": 200 * MB,
        "api_max_fetches": 40,
    }
    values.update(overrides)
    return SnapshotLimits(**values)


def build(data: bytes, lim: SnapshotLimits | None = None, deadline: Deadline | None = None):  # type: ignore[no-untyped-def]
    return build_archive_snapshot(
        chunked(data),
        full_name="o/r",
        default_branch="main",
        commit_sha="a" * 40,
        limits=lim or limits(),
        deadline=deadline or Deadline(60),
    )


def test_builds_snapshot_dropping_the_first_path_component() -> None:
    snap = build(make_tarball({"README.md": "# hi", "src/app/main.py": "print(1)"}))
    assert [e.path for e in snap.files()] == ["README.md", "src/app/main.py"]
    assert snap.read_text("README.md") == "# hi"
    assert snap.mode == "archive" and not snap.is_partial
    assert snap.commit_sha == "a" * 40 and snap.default_branch == "main"


def test_first_component_is_dropped_whatever_it_is_called() -> None:
    snap = build(make_tarball({"a.txt": "x"}, root="totally-different-name"))
    assert [e.path for e in snap.files()] == ["a.txt"]


def test_sizes_and_empty_files() -> None:
    snap = build(make_tarball({"empty.txt": "", "full.txt": "abc"}))
    sizes = {e.path: e.size for e in snap.files()}
    assert sizes == {"empty.txt": 0, "full.txt": 3}
    assert snap.read_text("empty.txt") == ""


def test_ignored_dirs_are_in_the_snapshot_but_filtered() -> None:
    snap = build(make_tarball({"node_modules/x.js": "x", "a.py": "x"}))
    assert [e.path for e in snap.files()] == ["a.py"]
    assert len(snap.files(include_ignored=True)) == 2


def test_malicious_archive_yields_only_regular_safe_files() -> None:
    def evil(tar: tarfile.TarFile) -> None:
        add_file(tar, "../evil.txt", b"x")  # traversal, no root folder
        add_file(tar, f"{ROOT}/../../escape.txt", b"x")  # traversal below the root
        add_file(tar, "/etc/passwd", b"x")  # absolute
        add_file(tar, f"{ROOT}/sub/../../up.txt", b"x")
        sym = tarfile.TarInfo(f"{ROOT}/link")
        sym.type, sym.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(sym)
        hard = tarfile.TarInfo(f"{ROOT}/hard")
        hard.type, hard.linkname = tarfile.LNKTYPE, f"{ROOT}/good.txt"
        tar.addfile(hard)
        dev = tarfile.TarInfo(f"{ROOT}/dev")
        dev.type = tarfile.CHRTYPE
        tar.addfile(dev)
        fifo = tarfile.TarInfo(f"{ROOT}/fifo")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)

    snap = build(make_tarball({"good.txt": "ok"}, extra=evil))
    assert [e.path for e in snap.files(include_ignored=True)] == ["good.txt"]
    assert snap.skipped_links == 4  # symlink, hardlink, device, fifo
    assert snap.skipped_unsafe == 4
    assert not snap.is_partial


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("root/a/b.txt", "a/b.txt"),
        ("./root/a.txt", "a.txt"),
        ("root/./a.txt", "a.txt"),
        ("root", None),
        ("root/", None),
        ("/abs/path", None),
        ("../x", None),
        ("root/../x", None),
        ("root/a/../../x", None),
        ("", None),
        ("root/a\x00b", None),
    ],
)
def test_safe_member_path(name: str, expected: str | None) -> None:
    assert safe_member_path(name) == expected


def test_later_duplicate_member_wins() -> None:
    def dup(tar: tarfile.TarFile) -> None:
        add_file(tar, f"{ROOT}/a.txt", b"second")

    snap = build(make_tarball({"a.txt": "first"}, extra=dup))
    assert snap.read_text("a.txt") == "second"
    assert len(snap.files()) == 1


def test_large_files_keep_an_entry_but_no_content() -> None:
    snap = build(make_tarball({"big.txt": "x" * 100, "small.txt": "x"}), limits(max_file_bytes=50))
    assert {e.path: e.size for e in snap.files()} == {"big.txt": 100, "small.txt": 1}
    assert snap.read_text("big.txt") is None
    assert snap.read_text("small.txt") == "x"
    assert not snap.is_partial  # an individually large file is not "partial"


def test_text_budget_exceeded_marks_partial_but_keeps_entries() -> None:
    files = {f"f{i}.txt": "x" * 40 for i in range(5)}
    snap = build(make_tarball(files), limits(max_text_total_bytes=100))
    assert len(snap.files()) == 5
    assert snap.is_partial and snap.partial_reasons == ["text_budget_exceeded"]
    loaded = [e.path for e in snap.files() if snap.read_text(e.path) is not None]
    assert len(loaded) == 2


def test_download_over_cap_raises_archive_too_large_and_closes_the_stream() -> None:
    closed = []

    def stream():  # type: ignore[no-untyped-def]
        try:
            for _ in range(100):
                yield b"x" * 1000
        finally:
            closed.append(True)

    with pytest.raises(ArchiveTooLarge):
        build_archive_snapshot(
            stream(),
            full_name="o/r",
            default_branch="main",
            commit_sha="a" * 40,
            limits=limits(max_archive_bytes=5000),
            deadline=Deadline(60),
        )
    assert closed == [True]


def test_deadline_is_checked_during_download() -> None:
    d = Deadline(60)

    def stream():  # type: ignore[no-untyped-def]
        yield b"x"
        d.expire()
        yield b"y"

    with pytest.raises(ScanTimeout):
        build_archive_snapshot(
            stream(),
            full_name="o/r",
            default_branch="main",
            commit_sha="a" * 40,
            limits=limits(),
            deadline=d,
        )


def test_deadline_is_checked_while_reading_members() -> None:
    d = Deadline(60)
    data = make_many_members_tarball(500)
    d.expire()
    # the download loop notices first
    with pytest.raises(ScanTimeout):
        build(data, deadline=d)


def test_garbage_or_truncated_archive_is_a_github_error() -> None:
    with pytest.raises(GitHubError):
        build(b"this is not a gzip file at all")
    good = make_tarball({"a.txt": "x" * 5000})
    with pytest.raises(GitHubError):
        build(good[: len(good) // 2])


def test_gzip_bomb_ends_partial_without_decompressing_it() -> None:
    """>= 1 GB of zeros in a tiny gzip: stop at the member header, bounded memory."""
    bomb = make_zero_bomb_tarball(1024 * MB)
    assert len(bomb) < 5 * MB  # really is a bomb: ~1 GB expands from a few MB
    tracemalloc.start()
    try:
        snap = build(bomb)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert snap.is_partial and "archive_limits" in snap.partial_reasons
    assert peak < 50 * MB
    assert [e.path for e in snap.files()] == ["README.md"]  # work done before the bomb is kept
    assert snap.read_text("README.md") == "hello"


def test_many_members_archive_is_capped_by_max_files() -> None:
    data = make_many_members_tarball(200_000)
    tracemalloc.start()
    try:
        snap = build(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert snap.is_partial and snap.partial_reasons == ["archive_limits"]
    assert len(snap.files()) == 100_000
    assert peak < 400 * MB


def test_declared_size_cap_counts_sum_of_members() -> None:
    files = {f"f{i}.bin": "x" * 100 for i in range(10)}
    snap = build(make_tarball(files), limits(max_uncompressed_bytes=450))
    assert snap.is_partial and snap.partial_reasons == ["archive_limits"]
    assert len(snap.files()) == 4


def test_empty_tarball_is_a_valid_empty_snapshot() -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz"):
        pass
    snap = build(raw.getvalue())
    assert snap.files() == [] and not snap.is_partial
