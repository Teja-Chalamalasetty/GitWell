import pytest

from app.scanner.globbing import compile_glob, glob_match


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("*.py", "main.py", True),
        ("*.py", "src/main.py", False),  # * does not cross /
        ("*.PY", "main.py", True),  # case-insensitive
        ("README.MD", "readme.md", True),
        ("test_?.py", "test_a.py", True),
        ("test_?.py", "test_ab.py", False),
        ("a?b", "a/b", False),  # ? does not cross /
        ("**/*.py", "main.py", True),  # **/ matches zero dirs
        ("**/*.py", "a/b/c/main.py", True),
        ("src/**/test.py", "src/test.py", True),
        ("src/**/test.py", "src/a/b/test.py", True),
        ("src/**/test.py", "other/test.py", False),
        ("docs/**", "docs/a/b.md", True),
        ("docs/**", "other/a.md", False),
        (".github/workflows/*.yml", ".github/workflows/ci.yml", True),
        (".github/workflows/*.yml", ".github/workflows/sub/ci.yml", False),  # direct children
        ("*.test.js", "a.test.js", True),
        ("*.test.js", "a_test_js", False),  # dots are literal
        ("[abc].txt", "b.txt", True),
        ("[!abc].txt", "b.txt", False),
        ("[!abc].txt", "d.txt", True),
        ("file[", "file[", True),  # unterminated class is literal
        ("a+b(c).txt", "a+b(c).txt", True),  # regex metacharacters are escaped
        ("a**b", "axxb", True),  # not a whole segment: behaves like *
        ("a**b", "ax/xb", False),
    ],
)
def test_glob_semantics(pattern: str, path: str, expected: bool) -> None:
    assert glob_match(pattern, path) is expected


def test_case_sensitive_mode() -> None:
    assert glob_match("*Test.java", "FooTest.java", case_sensitive=True)
    assert not glob_match("*Test.java", "footest.java", case_sensitive=True)
    assert glob_match("*Test.java", "footest.java")  # default is case-insensitive


def test_compiled_patterns_are_cached() -> None:
    assert compile_glob("*.py") is compile_glob("*.py")


def test_matching_does_not_depend_on_the_os_path_separator() -> None:
    """Backslashes are ordinary characters, never separators, on every OS."""
    assert not glob_match("src/*.py", "src\\main.py")
    assert glob_match("src\\*.py", "src\\main.py")
