"""Repositories: add, list (with summaries), get, delete."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.github.client import GitHubClient
from app.github.repo_meta import RepoMeta, parse_repo_payload
from app.github.url import parse_repo_url
from app.models.issue_draft import DraftStatus, IssueDraft
from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus
from app.models.user import User
from app.scanner.deadline import Deadline
from app.schemas.repo import LatestCompletedScan, LatestScan, RepoSummary

logger = logging.getLogger("gitwell.repos")

OPEN_DRAFT_STATUSES = (DraftStatus.drafted, DraftStatus.approved, DraftStatus.posting)
LOOKUP_TIMEOUT_SECONDS = 30
SortKey = Literal["name", "score", "last_scanned"]
SortOrder = Literal["asc", "desc"]


def _already_tracked() -> AppError:
    return AppError(409, "REPO_ALREADY_EXISTS", "This repository is already being tracked.")


def add_repo(db: Session, client: GitHubClient, url: str, user: User) -> Repo:
    """Validate the URL, look the repo up on GitHub, and insert it (PRD 5 US-1, 11.2)."""
    owner, name = parse_repo_url(url)
    # GitHub answers 301 for a renamed repo; the client follows it on the API host.
    meta = parse_repo_payload(client.get_repo(owner, name, Deadline(LOOKUP_TIMEOUT_SECONDS)))
    # Matched by github_id, so old and new names of a renamed repo are the same repo.
    if db.scalar(select(Repo.id).where(Repo.github_id == meta.github_id)) is not None:
        raise _already_tracked()
    repo = _new_repo(meta, user)
    db.add(repo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _already_tracked() from None
    return repo


def _new_repo(meta: RepoMeta, user: User) -> Repo:
    return Repo(
        github_id=meta.github_id,
        url=meta.url,
        owner=meta.owner,
        name=meta.name,
        full_name=meta.full_name,
        default_branch=meta.default_branch,
        is_private=meta.is_private,
        is_archived=meta.is_archived,
        has_issues=meta.has_issues,
        added_by_id=user.id,
    )


def get_repo(db: Session, repo_id: int) -> Repo:
    repo = db.get(Repo, repo_id)
    if repo is None:
        raise AppError(404, "NOT_FOUND", "Repository not found.")
    return repo


def delete_repo(db: Session, repo_id: int) -> None:
    """Deleting cascades to scans, results and drafts in the database. Allowed mid-scan."""
    repo = get_repo(db, repo_id)
    db.delete(repo)
    db.commit()


# ---- summaries -------------------------------------------------------------------------


def _ranked_scans(db: Session, repo_ids: Sequence[int], completed_only: bool) -> list[Scan]:
    """Newest-first scans per repo, ranked with a window function (1 = newest)."""
    rank = func.row_number().over(
        partition_by=Scan.repo_id, order_by=(Scan.created_at.desc(), Scan.id.desc())
    )
    query = select(Scan.id.label("scan_id"), rank.label("rank")).where(Scan.repo_id.in_(repo_ids))
    if completed_only:
        query = query.where(Scan.status == ScanStatus.completed)
    ranked = query.subquery()
    limit_rank = 2 if completed_only else 1
    rows = db.scalars(
        select(Scan)
        .join(ranked, Scan.id == ranked.c.scan_id)
        .where(ranked.c.rank <= limit_rank)
        .order_by(Scan.repo_id, ranked.c.rank)
    ).all()
    return list(rows)


def build_summaries(db: Session, repos: Sequence[Repo]) -> list[RepoSummary]:
    if not repos:
        return []
    ids = [r.id for r in repos]
    latest = {s.repo_id: s for s in _ranked_scans(db, ids, completed_only=False)}
    completed: dict[int, list[Scan]] = {}
    for scan in _ranked_scans(db, ids, completed_only=True):
        completed.setdefault(scan.repo_id, []).append(scan)
    active = {
        repo_id: scan_id
        for repo_id, scan_id in db.execute(
            select(Scan.repo_id, Scan.id).where(
                Scan.repo_id.in_(ids), Scan.status.in_((ScanStatus.queued, ScanStatus.running))
            )
        )
    }
    drafts = {
        repo_id: draft_id
        for repo_id, draft_id in db.execute(
            select(IssueDraft.repo_id, IssueDraft.id)
            .where(IssueDraft.repo_id.in_(ids), IssueDraft.status.in_(OPEN_DRAFT_STATUSES))
            .order_by(IssueDraft.id)
        )
    }
    newest_completed_ids = [scans[0].id for scans in completed.values()]
    with_findings = set(
        db.scalars(
            select(ScanResult.scan_id)
            .where(
                ScanResult.scan_id.in_(newest_completed_ids),
                ScanResult.status.in_((RuleStatus.fail, RuleStatus.warn)),
            )
            .distinct()
        ).all()
    )

    summaries: list[RepoSummary] = []
    for repo in repos:
        scans = completed.get(repo.id, [])
        newest = scans[0] if scans else None
        previous = scans[1] if len(scans) > 1 else None
        row = latest.get(repo.id)
        summaries.append(
            RepoSummary(
                id=repo.id,
                github_id=repo.github_id,
                url=repo.url,
                full_name=repo.full_name,
                default_branch=repo.default_branch,
                is_private=repo.is_private,
                is_archived=repo.is_archived,
                has_issues=repo.has_issues,
                added_at=repo.added_at,
                latest_scan=(
                    LatestScan(
                        id=row.id,
                        status=row.status,
                        error_code=row.error_code,
                        created_at=row.created_at,
                        finished_at=row.finished_at,
                    )
                    if row
                    else None
                ),
                latest_completed_scan=(
                    LatestCompletedScan(
                        id=newest.id,
                        score=newest.score,
                        grade=newest.grade,
                        commit_sha=newest.commit_sha,
                        finished_at=newest.finished_at,
                        rules_errored=newest.rules_errored,
                        is_partial=newest.is_partial,
                        has_findings=newest.id in with_findings,
                    )
                    if newest
                    else None
                ),
                score_delta=score_delta(newest, previous),
                active_scan_id=active.get(repo.id),
                open_draft_id=drafts.get(repo.id),
            )
        )
    return summaries


def score_delta(latest: Scan | None, previous: Scan | None) -> int | None:
    """Latest completed score minus the previous one (PRD 8.4).

    Null if either score is null or the two scans used different rule sets.
    """
    if latest is None or previous is None:
        return None
    if latest.score is None or previous.score is None:
        return None
    if latest.ruleset_version != previous.ruleset_version:
        return None
    return latest.score - previous.score


def summarize(db: Session, repo: Repo) -> RepoSummary:
    db.expire_all()  # a scan may have updated the row from another session
    return build_summaries(db, [repo])[0]


def list_summaries(
    db: Session, sort: SortKey, order: SortOrder, limit: int, offset: int
) -> tuple[list[RepoSummary], int]:
    """Sorted, paginated summaries. Nulls sort last in either direction; ties by full_name."""
    repos = list(db.scalars(select(Repo)).all())
    summaries = build_summaries(db, repos)
    summaries.sort(key=lambda s: s.full_name)  # the tie-break, kept by the stable sorts below
    descending = order == "desc"

    if sort == "name":
        summaries.sort(key=lambda s: s.full_name, reverse=descending)
    else:

        def value(s: RepoSummary) -> float | None:
            if sort == "score":
                done = s.latest_completed_scan
                return None if done is None or done.score is None else float(done.score)
            return None if s.latest_scan is None else s.latest_scan.created_at.timestamp()

        present = [s for s in summaries if value(s) is not None]
        missing = [s for s in summaries if value(s) is None]
        present.sort(key=lambda s: value(s) or 0.0, reverse=descending)
        summaries = present + missing
    return summaries[offset : offset + limit], len(summaries)
