"""No committed secrets (PRD 9.5).

PRIVACY (non-negotiable): the matched value is never stored, logged, returned or put in an issue.
A finding records only location, pattern id and confidence.
"""

from __future__ import annotations

import re
from typing import Any

from app.rules._util import basename
from app.rules.base import Rule, RuleResult, Status
from app.scanner.snapshot import RepoSnapshot

HIGH_CONFIDENCE = {
    "aws_access_key_id": r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])",
    "github_token": r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9_])",
    "github_fine_grained_pat": r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{60,255}(?![A-Za-z0-9_])",
    "slack_token": r"(?<![A-Za-z0-9-])xox[abprs]-[A-Za-z0-9-]{10,250}(?![A-Za-z0-9-])",
    "google_api_key": r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
    "stripe_live_key": r"(?<![A-Za-z0-9_])(?:sk|rk)_live_[0-9A-Za-z]{24,247}(?![0-9A-Za-z])",
    "private_key": r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|PGP|ENCRYPTED) )?PRIVATE KEY(?: BLOCK)?-----",
}
LOW_CONFIDENCE = {
    # key may be quoted (JSON/YAML); value must be quoted. Unquoted .env-style values are
    # intentionally not matched.
    "generic_secret_assignment": r"""(?i)["']?\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b["']?\s*[:=]\s*["']([^"'\s]{8,})["']""",
}
PLACEHOLDER_MARKERS = [
    "example", "changeme", "change_me", "placeholder", "your_", "your-", "xxxx",
    "dummy", "test", "sample", "fake", "<", "${", "{{", "os.environ", "process.env",
]  # fmt: skip
TEST_PATH_SEGMENTS = {
    "test", "tests", "__tests__", "spec", "specs", "fixtures", "testdata",
    "examples", "example", "docs",
}  # fmt: skip
ENV_FILE_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist"}

_HIGH = {name: re.compile(p) for name, p in HIGH_CONFIDENCE.items()}
_LOW = {name: re.compile(p) for name, p in LOW_CONFIDENCE.items()}

MAX_LINE_LENGTH = 10_000
MAX_FINDINGS_REPORTED = 100
MAX_FINDINGS_TRACKED = 10_000  # bounds memory on pathological files; scanning stops here
DEADLINE_EVERY = 100
_LOCK_BASENAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "go.sum",
}  # fmt: skip

FIX_TIP = (
    "**Revoke and rotate the credential first.** Then remove it from the code, load it from "
    "environment variables or a secret manager, and purge it from git history "
    "(`git filter-repo` or BFG). Deleting the file is not enough, because the secret stays "
    "in history."
)

Finding = tuple[str, int | None, str, str]  # path, line, pattern, confidence


def _skipped(path: str) -> bool:
    name = basename(path).lower()
    return name.endswith((".lock", ".min.js", ".map")) or name in _LOCK_BASENAMES


def _in_excluded_dir(path: str) -> bool:
    parts = path.split("/")[:-1]
    return ".git" in parts or "node_modules" in parts


def _is_env_file(name: str) -> bool:
    lowered = name.lower()
    return lowered == ".env" or (lowered.startswith(".env.") and lowered not in ENV_FILE_ALLOWED)


def _in_test_path(path: str) -> bool:
    return any(part.lower() in TEST_PATH_SEGMENTS for part in path.split("/")[:-1])


def _scan_text(path: str, text: str, found: set[Finding], counters: dict[str, int]) -> None:
    downgrade = _in_test_path(path)
    for number, line in enumerate(text.split("\n"), start=1):
        if len(line) > MAX_LINE_LENGTH:
            counters["lines_skipped_long"] += 1
            continue
        for pattern, regex in _HIGH.items():
            for match in regex.finditer(line):
                if "example" in match.group(0).lower():
                    continue
                found.add((path, number, pattern, "medium" if downgrade else "high"))
        for pattern, regex in _LOW.items():
            for match in regex.finditer(line):
                value = match.group(1).lower()
                if any(marker in value for marker in PLACEHOLDER_MARKERS):
                    continue
                found.add((path, number, pattern, "low"))
        if len(found) >= MAX_FINDINGS_TRACKED:
            return


class SecretsRule(Rule):
    id = "secrets"
    name = "No committed secrets"
    description = "No credentials are committed to the default branch."
    weight = 20
    display_order = 10
    fix_tip = FIX_TIP

    def check(self, snap: RepoSnapshot) -> RuleResult:
        counters = {
            "files_scanned": 0,
            "files_skipped_size": 0,
            "files_skipped_binary": 0,
            "lines_skipped_long": 0,
        }
        found: set[Finding] = set()
        content_scan = snap.mode != "api"  # API mode has no file contents beyond a prefetch set

        files = [
            e
            for e in snap.files(include_ignored=True)
            if not _in_excluded_dir(e.path) and not _skipped(e.path)
        ]
        for index, entry in enumerate(files):
            if index % DEADLINE_EVERY == 0:
                snap.check_deadline()
            if _is_env_file(basename(entry.path)):
                found.add((entry.path, None, "committed_env_file", "medium"))
            if not content_scan:
                continue
            text = snap.read_text(entry.path)
            if text is None:
                key = (
                    "files_skipped_size"
                    if entry.size > snap.max_file_bytes
                    else "files_skipped_binary"
                )
                counters[key] += 1
                continue
            counters["files_scanned"] += 1
            _scan_text(entry.path, text, found, counters)
            if len(found) >= MAX_FINDINGS_TRACKED:
                break

        ordered = sorted(found, key=lambda f: (f[0], f[1] if f[1] is not None else 0, f[2]))
        truncated = len(ordered) > MAX_FINDINGS_REPORTED
        findings: list[dict[str, Any]] = [
            {"path": p, "line": line, "pattern": pat, "confidence": conf}
            for p, line, pat, conf in ordered[:MAX_FINDINGS_REPORTED]
        ]
        details: dict[str, Any] = {
            "findings": findings,
            **counters,
            "truncated": truncated,
            "partial": snap.is_partial,
        }

        if ordered:
            n, f = len(ordered), len({finding[0] for finding in ordered})
            message = f"{n} potential secret(s) found in {f} file(s)."
            has_high = any(finding[3] == "high" for finding in ordered)
            return RuleResult(Status.FAIL if has_high else Status.WARN, message, details)
        if snap.is_partial:
            if snap.mode == "api":
                message = "Secret scan limited: repository too large for a full content scan."
            else:
                message = "Secret scan incomplete: the repository snapshot was partial."
            return RuleResult(Status.WARN, message, details)
        short_sha = snap.commit_sha[:7]
        return RuleResult(
            Status.PASS,
            f"No secrets found in the current tree of {snap.default_branch}@{short_sha}. "
            "Git history is not scanned.",
            details,
        )


rule = SecretsRule()
