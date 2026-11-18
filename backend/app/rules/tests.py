from __future__ import annotations

from app.rules._util import basename
from app.rules.base import Rule, RuleResult, Status
from app.scanner.globbing import compile_glob
from app.scanner.snapshot import RepoSnapshot

# Brace expansion is written out (PRD 9.3). Matched case-insensitively on the basename.
TEST_FILE_PATTERNS = (
    "test_*.py",
    "*_test.py",
    "*.test.js",
    "*.test.jsx",
    "*.test.ts",
    "*.test.tsx",
    "*.test.mjs",
    "*.test.cjs",
    "*.spec.js",
    "*.spec.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*_test.go",
    "*_spec.rb",
)
# Matched case-sensitively on the basename.
CASE_SENSITIVE_PATTERNS = ("*Test.java", "*Tests.java", "*Test.kt", "*Tests.cs")
TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs"})
SOURCE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".mjs",
        ".cjs",
        ".go",
        ".java",
        ".kt",
        ".cs",
        ".rb",
        ".rs",
        ".php",
        ".swift",
    }  # fmt: skip
)

_INSENSITIVE = tuple(compile_glob(p) for p in TEST_FILE_PATTERNS)
_SENSITIVE = tuple(compile_glob(p, case_sensitive=True) for p in CASE_SENSITIVE_PATTERNS)
SAMPLE_SIZE = 5


def _matches_test_pattern(path: str) -> bool:
    name = basename(path)
    return any(p.fullmatch(name) for p in _INSENSITIVE) or any(
        p.fullmatch(name) for p in _SENSITIVE
    )


def _is_source(path: str) -> bool:
    name = basename(path)
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in SOURCE_EXTENSIONS


def _under_test_dir(path: str) -> bool:
    return any(part.lower() in TEST_DIR_NAMES for part in path.split("/")[:-1])


class TestsRule(Rule):
    __test__ = False  # not a pytest test class, despite the name

    id = "tests"
    name = "Tests"
    description = "The repository contains automated tests."
    weight = 15
    display_order = 30
    fix_tip = (
        "Add a test framework (pytest, Vitest/Jest, `go test`, …) and at least one test, "
        "and run it in CI."
    )

    def check(self, snap: RepoSnapshot) -> RuleResult:
        files = snap.files()
        test_files = sorted(
            e.path
            for e in files
            if _matches_test_pattern(e.path) or (_is_source(e.path) and _under_test_dir(e.path))
        )
        if test_files:
            return RuleResult(
                Status.PASS,
                f"Found {len(test_files)} test file(s).",
                {"test_file_count": len(test_files), "sample": test_files[:SAMPLE_SIZE]},
            )
        details = {"test_file_count": 0, "sample": []}
        if any(d.rsplit("/", 1)[-1].lower() in TEST_DIR_NAMES for d in snap.dirs()):
            return RuleResult(
                Status.WARN,
                "A test directory exists but contains no source files.",
                details,
            )
        return RuleResult(Status.FAIL, "No test files or test directories found.", details)


rule = TestsRule()
