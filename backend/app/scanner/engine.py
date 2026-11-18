"""Rule engine and snapshot builder (PRD 8.1, 8.3)."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.text import truncate
from app.github.client import GitHubClient
from app.github.errors import GitHubRepoTooLarge
from app.rules.base import Rule, Status
from app.scanner.api_snapshot import build_api_snapshot
from app.scanner.archive_snapshot import ArchiveTooLarge, SnapshotLimits, build_archive_snapshot
from app.scanner.deadline import Deadline, ScanTimeout
from app.scanner.scoring import ScoredRule
from app.scanner.snapshot import RepoSnapshot

logger = logging.getLogger("gitwell.scanner")

RULE_ERROR_MESSAGE = "Rule failed to run."
MESSAGE_LIMIT = 1000


@dataclass
class RuleOutcome:
    rule_id: str
    name: str
    weight: int
    display_order: int
    status: Status
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def scored(self) -> ScoredRule:
        return ScoredRule(self.rule_id, self.weight, self.status)


def run_rules(
    snapshot: RepoSnapshot, rules: Sequence[Rule], deadline: Deadline
) -> list[RuleOutcome]:
    """Run every rule in display order, isolating failures.

    A rule that raises is recorded as `error` and the scan continues. Only the exception TYPE and
    stack frames are logged, never its message, which could contain repository content.
    A ScanTimeout is never swallowed.
    """
    outcomes: list[RuleOutcome] = []
    for rule in sorted(rules, key=lambda r: r.display_order):
        deadline.check()
        try:
            result = rule.check(snapshot)
            status, message, details = result.status, result.message, result.details
        except ScanTimeout:
            raise
        except Exception as exc:
            logger.error(
                "rule_failed",
                extra={
                    "rule_id": rule.id,
                    "exc_type": type(exc).__name__,
                    "stack": "".join(traceback.format_tb(exc.__traceback__)),
                },
            )
            status, message, details = Status.ERROR, RULE_ERROR_MESSAGE, {}
        outcomes.append(
            RuleOutcome(
                rule_id=rule.id,
                name=rule.name,
                weight=rule.weight,
                display_order=rule.display_order,
                status=status,
                message=truncate(message, MESSAGE_LIMIT),
                details=details,
            )
        )
    return outcomes


@dataclass(frozen=True)
class RepoInfo:
    owner: str
    name: str
    full_name: str
    default_branch: str
    size_kb: int


def build_snapshot(
    client: GitHubClient,
    repo: RepoInfo,
    commit_sha: str,
    limits: SnapshotLimits,
    deadline: Deadline,
) -> RepoSnapshot:
    """Choose archive or API mode (PRD 8.2).

    API mode needs many calls, so without a token it is not allowed: the scan fails with
    REPO_TOO_LARGE instead.
    """
    too_big = repo.size_kb * 1024 > limits.max_archive_bytes

    def api() -> RepoSnapshot:
        if not client.has_token:
            raise GitHubRepoTooLarge
        return build_api_snapshot(
            client,
            owner=repo.owner,
            name=repo.name,
            full_name=repo.full_name,
            default_branch=repo.default_branch,
            commit_sha=commit_sha,
            limits=limits,
            deadline=deadline,
        )

    if too_big:
        return api()
    try:
        chunks = client.stream_tarball(repo.owner, repo.name, commit_sha, deadline)
        return build_archive_snapshot(
            chunks,
            full_name=repo.full_name,
            default_branch=repo.default_branch,
            commit_sha=commit_sha,
            limits=limits,
            deadline=deadline,
        )
    except ArchiveTooLarge:
        return api()
