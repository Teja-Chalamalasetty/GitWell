import pytest

from app.scanner.deadline import Deadline, ScanTimeout
from app.scanner.snapshot import IGNORED_DIRS, BaseSnapshot, FileEntry
from tests.fakes import FakeSnapshot, Unreadable


def paths(entries: list[FileEntry]) -> list[str]:
    return [e.path for e in entries]


def test_files_are_sorted_and_ignore_dirs_at_any_depth() -> None:
    snap = FakeSnapshot(
        {
            "z.py": "",
            "a.py": "",
            "node_modules/x/index.js": "",
            "src/vendor/lib.py": "",
            "pkg/.venv/bin/python": "",
            "build/out.js": "",
            "src/__pycache__/m.pyc": "",
            "keep/me.txt": "",
        }
    )
    assert paths(snap.files()) == ["a.py", "keep/me.txt", "z.py"]
    assert len(snap.files(include_ignored=True)) == 8


def test_ignored_dir_names_only_apply_to_directories_not_files() -> None:
    snap = FakeSnapshot({"build": "a file named build", "dist": "x"})
    assert paths(snap.files()) == ["build", "dist"]


@pytest.mark.parametrize("name", sorted(IGNORED_DIRS))
def test_every_ignored_dir_is_excluded(name: str) -> None:
    snap = FakeSnapshot({f"{name}/f.txt": "x", "ok.txt": "x"})
    assert paths(snap.files()) == ["ok.txt"]
    assert name not in snap.dirs()
    assert name in snap.dirs(include_ignored=True)


def test_dirs_derived_from_file_paths() -> None:
    snap = FakeSnapshot({"a/b/c.py": "", "a/d.py": "", "e.py": ""})
    assert snap.dirs() == {"a", "a/b"}


def test_root_file_is_case_insensitive_and_root_only() -> None:
    snap = FakeSnapshot({"readme.MD": "x", "docs/README.md": "x"})
    entry = snap.root_file(["README.md"])
    assert entry is not None and entry.path == "readme.MD"
    assert snap.root_file(["CONTRIBUTING.md"]) is None


def test_root_file_prefers_earlier_name_then_path() -> None:
    snap = FakeSnapshot({"README": "x", "README.md": "x", "readme.txt": "x"})
    entry = snap.root_file(["README.md", "README", "README.txt"])
    assert entry is not None and entry.path == "README.md"
    snap2 = FakeSnapshot({"Readme.md": "x", "README.md": "x"})
    entry2 = snap2.root_file(["readme.md"])
    assert entry2 is not None and entry2.path == "README.md"  # tie broken by path


def test_glob_is_case_insensitive_and_respects_ignored_dirs() -> None:
    snap = FakeSnapshot({"A.PY": "", "src/b.py": "", "node_modules/c.py": ""})
    assert paths(snap.glob("*.py")) == ["A.PY"]
    assert paths(snap.glob("**/*.py")) == ["A.PY", "src/b.py"]
    assert paths(snap.glob("**/*.py", include_ignored=True)) == [
        "A.PY",
        "node_modules/c.py",
        "src/b.py",
    ]


def test_read_text_decodes_utf8_and_strips_bom() -> None:
    snap = FakeSnapshot({"a.txt": b"\xef\xbb\xbfhello \xc3\xa9"})
    assert snap.read_text("a.txt") == "hello é"


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe\x00\x00 not utf8",  # invalid UTF-8
        b"abc\x00def",  # NUL in the first 8 KB
    ],
)
def test_read_text_returns_none_for_binary_or_non_utf8(content: bytes) -> None:
    assert FakeSnapshot({"a.bin": content}).read_text("a.bin") is None


def test_nul_after_first_8kb_is_still_text() -> None:
    assert FakeSnapshot({"a.txt": b"a" * 9000 + b"\x00"}).read_text("a.txt") is not None


def test_read_text_none_when_too_large_or_not_loaded() -> None:
    snap = FakeSnapshot({"big.txt": "x" * 20, "gone.txt": Unreadable(5)}, max_file_bytes=10)
    assert snap.read_text("big.txt") is None
    assert snap.read_text("gone.txt") is None
    assert snap.read_text("missing.txt") is None


def test_partial_flag_follows_reasons() -> None:
    assert not FakeSnapshot({}).is_partial
    snap = FakeSnapshot({}, partial_reasons=("b", "a", "a"))
    assert snap.is_partial and snap.partial_reasons == ["a", "b"]


def test_snapshot_is_immutable_to_callers() -> None:
    snap = FakeSnapshot({"a.py": ""})
    snap.files().clear()
    assert paths(snap.files()) == ["a.py"]


def test_check_deadline() -> None:
    d = Deadline(60)
    snap = BaseSnapshot(
        entries=[],
        contents={},
        full_name="o/r",
        default_branch="main",
        commit_sha="a",
        mode="api",
        deadline=d,
    )
    snap.check_deadline()
    d.expire()
    with pytest.raises(ScanTimeout):
        snap.check_deadline()
