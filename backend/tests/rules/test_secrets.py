"""Secret-shaped strings are built at runtime so this repo never contains one (PRD 16)."""

import json

import pytest

from app.rules.base import Status
from app.rules.secrets import rule
from app.scanner.deadline import ScanTimeout
from tests.fakes import FakeSnapshot, Unreadable

AWS = "AKIA" + "Q" * 16
GH = "gh" + "p_" + "a" * 36
FINE = "github_" + "pat_" + "A" * 60
SLACK = "xox" + "b-" + "1" * 12
GOOGLE = "AI" + "za" + "B" * 35
STRIPE = "sk_" + "live_" + "a" * 24
PEM = "-----BEGIN " + "RSA PRIVATE KEY-----"


def scan(files: dict[str, str], **kw: object):  # type: ignore[no-untyped-def]
    return rule.check(FakeSnapshot(files, **kw))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("secret", "pattern"),
    [
        (AWS, "aws_access_key_id"),
        ("ASIA" + "Z" * 16, "aws_access_key_id"),
        (GH, "github_token"),
        ("gho_" + "b" * 36, "github_token"),
        ("ghs_" + "c" * 40, "github_token"),
        (FINE, "github_fine_grained_pat"),
        (SLACK, "slack_token"),
        (GOOGLE, "google_api_key"),
        (STRIPE, "stripe_live_key"),
        ("rk_" + "live_" + "z" * 30, "stripe_live_key"),
        (PEM, "private_key"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
        ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private_key"),
        ("-----BEGIN " + "PGP PRIVATE KEY BLOCK-----", "private_key"),
    ],
)
def test_high_confidence_patterns_fail(secret: str, pattern: str) -> None:
    result = scan({"app/config.py": f"x = 1\nKEY = '{secret}'\n"})
    assert result.status is Status.FAIL
    assert result.details["findings"] == [
        {"path": "app/config.py", "line": 2, "pattern": pattern, "confidence": "high"}
    ]
    assert result.message == "1 potential secret(s) found in 1 file(s)."


@pytest.mark.parametrize(
    "text",
    [
        "X" + AWS,  # preceded by an alphanumeric: lookbehind
        AWS + "X",  # followed by an alphanumeric: lookahead
        "AKIA" + "Q" * 15,  # too short
        "ghp_" + "a" * 35,  # too short
        "xoxz-" + "1" * 12,  # unknown slack type
        "sk_test_" + "a" * 24,  # not a live key
        "-----BEGIN " + "PUBLIC KEY-----",
        "-----BEGIN " + "CERTIFICATE-----",
    ],
)
def test_near_misses_do_not_match(text: str) -> None:
    assert scan({"a.txt": text}).status is Status.PASS


def test_example_keys_are_ignored_case_insensitively() -> None:
    sample = "AKIA" + "IOSFODNN7" + "EXAMPLE"  # AWS's documented sample key
    assert scan({"a.txt": sample, "b.txt": sample.lower().upper()}).status is Status.PASS
    assert scan({"a.txt": "ghp_" + "a" * 20 + "ExAmPlE" + "b" * 9}).status is Status.PASS


def test_high_confidence_match_in_test_paths_is_downgraded_to_medium() -> None:
    for path in (
        "tests/data.py",
        "src/fixtures/k.txt",
        "docs/guide.md",
        "a/Examples/k.js",
        "pkg/__tests__/x.ts",
        "spec/k.rb",
        "testdata/k.json",
    ):
        result = scan({path: AWS})
        assert result.status is Status.WARN, path
        assert result.details["findings"][0]["confidence"] == "medium"


def test_test_path_segments_must_be_directories_not_the_filename() -> None:
    assert scan({"test.py": AWS}).status is Status.FAIL
    assert scan({"tests.txt": AWS}).status is Status.FAIL


def test_high_wins_over_medium_in_the_same_scan() -> None:
    result = scan({"tests/a.py": AWS, "src/b.py": GH})
    assert result.status is Status.FAIL
    assert result.message == "2 potential secret(s) found in 2 file(s)."


# ---- generic (low confidence) --------------------------------------------------------------


def assign(key: str, value: str, sep: str = " = ", quote: str = '"') -> str:
    """Built at runtime so this file never contains an assignment-shaped secret literal."""
    return f"{key}{sep}{quote}{value}{quote}"


@pytest.mark.parametrize(
    "line",
    [
        assign("pass" + "word", "hunter2" * 2),
        assign("PASS" + "WORD", "correct" + "horse99", sep=": ", quote="'"),
        assign('"api_' + 'key"', "abcdef123456", sep=": "),
        assign("client_" + "secret", "s3cr3t" + "value!", sep="="),
        assign("auth-" + "token", "zzzzzzzzzz", quote="'"),
        assign("API_" + "KEY", "Xk29dkw93kd", sep="="),
    ],
)
def test_generic_quoted_assignments_warn(line: str) -> None:
    result = scan({"src/settings.py": line})
    assert result.status is Status.WARN
    assert result.details["findings"][0]["pattern"] == "generic_secret_assignment"
    assert result.details["findings"][0]["confidence"] == "low"


@pytest.mark.parametrize(
    "line",
    [
        'password = "changeme123"',
        'password = "your_password_here"',
        'api_key = "YOUR-API-KEY-HERE"',
        'secret = "<insert-secret>"',
        'password = "${DB_PASSWORD}"',
        'token = "{{ vault_token }}"',
        'api_key = "os.environ.get"',
        'password = "mytestpassword"',
        'password = "dummy-value-1"',
        'secret = "sample-secret-1"',
        'secret = "fake-secret-value"',
        'api_key = "xxxxxxxxxxxx"',
        'password = "short"',  # < 8 characters
        "PASSWORD=unquotedvalue123",  # unquoted .env style is intentionally not matched
        'password = "has space inside"',
    ],
)
def test_placeholders_short_and_unquoted_values_are_ignored(line: str) -> None:
    assert scan({"src/settings.py": line}).status is Status.PASS


# ---- committed .env files ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", [".env", "config/.env", ".env.local", "svc/api/.env.production", ".ENV"]
)
def test_committed_env_files_warn_with_a_file_level_finding(path: str) -> None:
    result = scan({path: "A=1"})
    assert result.status is Status.WARN
    assert result.details["findings"] == [
        {"path": path, "line": None, "pattern": "committed_env_file", "confidence": "medium"}
    ]


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".env.sample",
        ".env.template",
        ".env.dist",
        "environment.py",
        "a.env",
        ".environment",
    ],
)
def test_allowed_or_unrelated_names_are_not_env_findings(path: str) -> None:
    assert scan({path: "A=1"}).status is Status.PASS


def test_a_real_key_inside_an_env_file_is_its_own_high_finding() -> None:
    result = scan({".env.development": f"AWS={AWS}"})
    assert result.status is Status.FAIL
    patterns = [f["pattern"] for f in result.details["findings"]]
    assert patterns == ["committed_env_file", "aws_access_key_id"]  # None line sorts first


# ---- scope ---------------------------------------------------------------------------------


def test_scans_ignored_dirs_but_never_git_or_node_modules() -> None:
    assert scan({"vendor/lib/k.py": AWS}).status is Status.FAIL
    assert scan({"build/out.js": AWS}).status is Status.FAIL
    assert scan({".git/config": AWS}).status is Status.PASS
    assert scan({"node_modules/pkg/k.js": AWS}).status is Status.PASS
    assert scan({"sub/node_modules/pkg/k.js": AWS, "x/.git/y": AWS}).status is Status.PASS
    assert scan({"node_modules/.env": "A=1"}).status is Status.PASS


@pytest.mark.parametrize(
    "path",
    [
        "poetry.lock",
        "Cargo.lock",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "go.sum",
        "app.min.js",
        "bundle.js.map",
        "sub/PACKAGE-LOCK.JSON",
    ],
)
def test_lock_files_minified_and_source_maps_are_skipped(path: str) -> None:
    assert scan({path: AWS}).status is Status.PASS


def test_unreadable_files_are_counted_not_scanned() -> None:
    snap = FakeSnapshot(
        {"big.txt": Unreadable(600_000), "bin.dat": b"\x00\x01" + AWS.encode(), "ok.txt": "x"}
    )
    result = rule.check(snap)
    assert result.status is Status.PASS
    assert result.details["files_scanned"] == 1
    assert result.details["files_skipped_size"] == 1
    assert result.details["files_skipped_binary"] == 1


def test_very_long_lines_are_skipped_and_counted() -> None:
    long_line = "a" * 10_001 + AWS
    result = scan({"min.txt": long_line + "\nshort line"})
    assert result.status is Status.PASS
    assert result.details["lines_skipped_long"] == 1
    assert scan({"x.txt": "a" * 9_000 + " " + AWS}).status is Status.FAIL


def test_line_numbers_are_one_based_and_ignore_exotic_line_breaks() -> None:
    text = "a\r\nb\x0bstill line 2 still line 2\nc " + AWS
    assert scan({"f.txt": text}).details["findings"][0]["line"] == 3


def test_one_finding_per_path_line_and_pattern() -> None:
    result = scan({"f.py": f"{AWS} {AWS}\n{AWS}"})
    assert [f["line"] for f in result.details["findings"]] == [1, 2]


def test_same_line_can_match_different_patterns() -> None:
    result = scan({"f.py": f'api_key = "{AWS}"'})
    assert {f["pattern"] for f in result.details["findings"]} == {
        "aws_access_key_id",
        "generic_secret_assignment",
    }
    assert result.message == "2 potential secret(s) found in 1 file(s)."


def test_findings_are_sorted_by_path_then_line() -> None:
    result = scan({"b.py": f"x\n{AWS}", "a.py": f"{AWS}\n\n{GH}"})
    got = [(f["path"], f["line"]) for f in result.details["findings"]]
    assert got == [("a.py", 1), ("a.py", 3), ("b.py", 2)]


def test_findings_are_capped_at_100_but_the_message_counts_all() -> None:
    text = "\n".join(AWS for _ in range(150))
    result = scan({"many.txt": text})
    assert len(result.details["findings"]) == 100
    assert result.details["truncated"] is True
    assert result.message == "150 potential secret(s) found in 1 file(s)."
    assert scan({"few.txt": "\n".join([AWS] * 3)}).details["truncated"] is False


# ---- result messages and partial snapshots -------------------------------------------------


def test_pass_message_names_branch_and_short_sha_and_history_caveat() -> None:
    result = scan({"a.txt": "clean"}, default_branch="trunk", commit_sha="1234567890abcdef")
    assert result.status is Status.PASS
    assert result.message == (
        "No secrets found in the current tree of trunk@1234567. Git history is not scanned."
    )
    assert result.details["partial"] is False


def test_partial_archive_snapshot_warns_even_without_findings() -> None:
    result = scan({"a.txt": "clean"}, is_partial=True, partial_reasons=("archive_limits",))
    assert result.status is Status.WARN
    assert result.details["partial"] is True
    assert "incomplete" in result.message


def test_api_mode_checks_only_env_files_and_says_so() -> None:
    clean = scan({"a.txt": AWS}, mode="api", is_partial=True, partial_reasons=("api_mode",))
    assert clean.status is Status.WARN  # content is never scanned in API mode
    assert clean.message == "Secret scan limited: repository too large for a full content scan."
    assert clean.details["findings"] == []
    assert clean.details["files_scanned"] == 0

    with_env = scan({".env": "A=1"}, mode="api", is_partial=True, partial_reasons=("api_mode",))
    assert with_env.status is Status.WARN
    assert with_env.message == "1 potential secret(s) found in 1 file(s)."


def test_findings_present_in_a_partial_scan_keep_the_normal_message() -> None:
    result = scan({"a.txt": AWS}, is_partial=True, partial_reasons=("text_budget_exceeded",))
    assert result.status is Status.FAIL


# ---- privacy -------------------------------------------------------------------------------


def test_matched_values_never_appear_in_the_result() -> None:
    secrets = [AWS, GH, FINE, SLACK, GOOGLE, STRIPE, PEM, "hunter2" * 2]
    generic = assign("pass" + "word", "hunter2" * 2)
    files = {f"src/f{i}.py": f'token = "{s}"\nx = "{s}"\n{generic}' for i, s in enumerate(secrets)}
    result = scan(files)
    blob = json.dumps({"message": result.message, "details": result.details})
    for secret in secrets:
        assert secret not in blob
    assert set(result.details["findings"][0]) == {"path", "line", "pattern", "confidence"}


# ---- deadline ------------------------------------------------------------------------------


def test_deadline_is_checked_every_100_files() -> None:
    snap = FakeSnapshot({f"f{i:04}.txt": "x" for i in range(250)})
    calls: list[int] = []
    snap.check_deadline = lambda: calls.append(1)  # type: ignore[method-assign]
    rule.check(snap)
    assert len(calls) == 3  # before files 0, 100 and 200


def test_timeout_propagates_out_of_the_rule() -> None:
    snap = FakeSnapshot({f"f{i}.txt": "x" for i in range(150)})

    def expire() -> None:
        raise ScanTimeout

    snap.check_deadline = expire  # type: ignore[method-assign]
    with pytest.raises(ScanTimeout):
        rule.check(snap)


def test_metadata() -> None:
    assert (rule.id, rule.weight, rule.display_order) == ("secrets", 20, 10)
    assert rule.fix_tip.startswith("**Revoke and rotate the credential first.**")
    assert "git filter-repo" in rule.fix_tip
