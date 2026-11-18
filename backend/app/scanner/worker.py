"""Scan worker (PRD 6.1, 8.1).

Workers receive only a scan id. Each opens its own session and uses short transactions:

1. claim: ``queued -> running`` as a conditional UPDATE, then commit (0 rows: exit)
2. talk to GitHub and run the rules with NO open transaction
3. write results + final status in ONE transaction, as a conditional UPDATE
   (``WHERE status='running'``); 0 rows means the scan was deleted meanwhile, so the results
   are discarded and ``scan_orphaned`` is logged at INFO.

A scan row's ``error_message`` always comes from the fixed catalog, never from an exception.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.core.metrics import get_metrics
from app.db import utcnow
from app.github.client import GitHubClient
from app.github.errors import GitHubRepoDisabled
from app.github.repo_meta import RepoMeta, parse_repo_payload
from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus, ScanTrigger
from app.rules.base import Rule
from app.rules.registry import get_rules
from app.scanner.archive_snapshot import SnapshotLimits
from app.scanner.deadline import Deadline
from app.scanner.engine import RepoInfo, RuleOutcome, build_snapshot, run_rules
from app.scanner.errors import SCAN_ERRORS, scan_error_code
from app.scanner.scoring import compute_score, ruleset_version

logger = logging.getLogger("gitwell.scans")


@dataclass
class _Progress:
    """What is known about a scan so far, to record on failure."""

    repo_id: int
    trigger: ScanTrigger = ScanTrigger.manual
    started: float = 0.0  # time.monotonic() when the scan was claimed
    meta: RepoMeta | None = None
    commit_sha: str | None = None


@dataclass
class _ScanOutcome:
    outcomes: list[RuleOutcome]
    snapshot_mode: str
    is_partial: bool


def _stack(exc: BaseException) -> str:
    # Frames only: the exception message could contain repository content (S7).
    return "".join(traceback.format_tb(exc.__traceback__))


class ScanRunner:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        client_factory: Callable[[], GitHubClient],
        rules_provider: Callable[[], Sequence[Rule]] = get_rules,
    ) -> None:
        self._settings = settings
        self._sessions = session_factory
        self._client_factory = client_factory
        self._rules = rules_provider
        self._limits = SnapshotLimits.from_settings(settings)
        self._deadlines: dict[int, Deadline] = {}
        self._lock = threading.Lock()

    # ---- shutdown support (PRD 14) -----------------------------------------------------

    def expire_all(self) -> None:
        """Make every running scan's deadline expire now, so they abort within seconds."""
        with self._lock:
            for deadline in self._deadlines.values():
                deadline.expire()

    # ---- entry point ---------------------------------------------------------------------

    def run(self, scan_id: int) -> None:
        """Run one scan. Never raises: executor threads would swallow it silently."""
        try:
            self._run(scan_id)
        except Exception as exc:
            logger.error(
                "scan_worker_crashed",
                extra={"scan_id": scan_id, "exc_type": type(exc).__name__, "stack": _stack(exc)},
            )
            try:
                self._write_failed(scan_id, _Progress(repo_id=0), "INTERNAL_ERROR")
            except Exception as inner:
                logger.error(
                    "scan_failure_not_recorded",
                    extra={"scan_id": scan_id, "exc_type": type(inner).__name__},
                )

    # ---- internals -------------------------------------------------------------------------

    def _claim(self, scan_id: int) -> tuple[int, int, ScanTrigger] | None:
        """Step 1. Returns (repo_id, github_id, trigger) if this worker won the scan."""
        with self._sessions() as s:
            claim = cast(
                "CursorResult[Any]",
                s.execute(
                    update(Scan)
                    .where(Scan.id == scan_id, Scan.status == ScanStatus.queued)
                    .values(status=ScanStatus.running, started_at=utcnow())
                ),
            )
            s.commit()
            if claim.rowcount == 0:
                return None
            row = s.execute(
                select(Scan.repo_id, Repo.github_id, Scan.trigger)
                .join(Repo, Repo.id == Scan.repo_id)
                .where(Scan.id == scan_id)
            ).first()
            s.rollback()  # end the read transaction before talking to GitHub
        if row is None:  # pragma: no cover - only if the repo is deleted between UPDATE and SELECT
            return None
        return row[0], row[1], row[2]

    def _run(self, scan_id: int) -> None:
        claimed = self._claim(scan_id)
        if claimed is None:
            return
        repo_id, github_id, trigger = claimed
        logger.info("scan_started", extra={"scan_id": scan_id, "repo_id": repo_id})

        progress = _Progress(repo_id=repo_id, trigger=trigger, started=time.monotonic())
        deadline = Deadline(self._settings.scan_timeout_seconds)
        with self._lock:
            self._deadlines[scan_id] = deadline
        try:
            outcome = self._scan(github_id, progress, deadline)
        except Exception as exc:
            code = scan_error_code(exc)
            if code == "INTERNAL_ERROR":
                logger.error(
                    "scan_internal_error",
                    extra={
                        "scan_id": scan_id,
                        "exc_type": type(exc).__name__,
                        "stack": _stack(exc),
                    },
                )
            self._write_failed(scan_id, progress, code)
            return
        finally:
            with self._lock:
                self._deadlines.pop(scan_id, None)
        self._write_completed(scan_id, progress, outcome)

    def _scan(self, github_id: int, progress: _Progress, deadline: Deadline) -> _ScanOutcome:
        """Steps 2-6: GitHub, snapshot, rules. No database transaction is open here."""
        client = self._client_factory()
        try:
            meta = parse_repo_payload(client.get_repo_by_id(github_id, deadline))
            progress.meta = meta
            if meta.disabled:
                raise GitHubRepoDisabled
            progress.commit_sha = client.get_commit_sha(
                meta.owner, meta.name, meta.default_branch, deadline
            )
            snapshot = build_snapshot(
                client,
                RepoInfo(meta.owner, meta.name, meta.full_name, meta.default_branch, meta.size_kb),
                progress.commit_sha,
                self._limits,
                deadline,
            )
            outcomes = run_rules(snapshot, self._rules(), deadline)
            return _ScanOutcome(outcomes, snapshot.mode, snapshot.is_partial)
        finally:
            client.close()

    def _refresh_repo(self, s: Session, repo_id: int, meta: RepoMeta) -> None:
        """Refresh owner/name/etc. from GitHub (PRD 10). Skips renames that would collide."""
        values: dict[str, Any] = {
            "default_branch": meta.default_branch,
            "is_private": meta.is_private,
            "is_archived": meta.is_archived,
            "has_issues": meta.has_issues,
        }
        clash = s.scalar(
            select(Repo.id).where(Repo.full_name == meta.full_name, Repo.id != repo_id)
        )
        if clash is None:
            values.update(owner=meta.owner, name=meta.name, full_name=meta.full_name, url=meta.url)
        else:
            logger.warning("repo_rename_skipped", extra={"repo_id": repo_id})
        s.execute(update(Repo).where(Repo.id == repo_id).values(**values))

    @staticmethod
    def _observe(progress: _Progress, status: str) -> None:
        """Count a finished scan (never an orphaned one) and record how long it ran."""
        metrics = get_metrics()
        metrics.scans.labels(status, progress.trigger.value).inc()
        if progress.started:
            metrics.scan_duration.observe(time.monotonic() - progress.started)

    def _orphaned(self, s: Session, scan_id: int, repo_id: int) -> None:
        s.rollback()
        logger.info("scan_orphaned", extra={"scan_id": scan_id, "repo_id": repo_id})

    def _write_completed(self, scan_id: int, progress: _Progress, outcome: _ScanOutcome) -> None:
        """Step 7: results + score + final status in one transaction, conditional on running."""
        rules = self._rules()
        score = compute_score([o.scored() for o in outcome.outcomes])
        with self._sessions() as s:
            done = cast(
                "CursorResult[Any]",
                s.execute(
                    update(Scan)
                    .where(Scan.id == scan_id, Scan.status == ScanStatus.running)
                    .values(
                        status=ScanStatus.completed,
                        score=score.score,
                        grade=score.grade,
                        rules_errored=score.rules_errored,
                        ruleset_version=ruleset_version(rules),
                        commit_sha=progress.commit_sha,
                        snapshot_mode=outcome.snapshot_mode,
                        is_partial=outcome.is_partial,
                        finished_at=utcnow(),
                    )
                ),
            )
            if done.rowcount == 0:
                self._orphaned(s, scan_id, progress.repo_id)
                return
            s.add_all(
                ScanResult(
                    scan_id=scan_id,
                    rule_id=o.rule_id,
                    status=RuleStatus(o.status.value),
                    message=o.message,
                    details=o.details,
                    weight=o.weight,
                    display_order=o.display_order,
                )
                for o in outcome.outcomes
            )
            if progress.meta is not None:
                self._refresh_repo(s, progress.repo_id, progress.meta)
            s.commit()
        self._observe(progress, "completed")
        for o in outcome.outcomes:
            get_metrics().rule_results.labels(o.rule_id, o.status.value).inc()
        logger.info(
            "scan_completed",
            extra={"scan_id": scan_id, "repo_id": progress.repo_id, "score": score.score},
        )

    def _write_failed(self, scan_id: int, progress: _Progress, code: str) -> None:
        with self._sessions() as s:
            failed = cast(
                "CursorResult[Any]",
                s.execute(
                    update(Scan)
                    .where(Scan.id == scan_id, Scan.status == ScanStatus.running)
                    .values(
                        status=ScanStatus.failed,
                        error_code=code,
                        error_message=SCAN_ERRORS[code],
                        commit_sha=progress.commit_sha,
                        finished_at=utcnow(),
                    )
                ),
            )
            if failed.rowcount == 0:
                self._orphaned(s, scan_id, progress.repo_id)
                return
            if progress.meta is not None and progress.repo_id:
                self._refresh_repo(s, progress.repo_id, progress.meta)
            s.commit()
        self._observe(progress, "failed")
        logger.info(
            "scan_failed",
            extra={"scan_id": scan_id, "repo_id": progress.repo_id, "error_code": code},
        )
