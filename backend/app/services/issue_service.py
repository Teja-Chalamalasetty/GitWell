"""Issue drafts: create, edit, discard, and approve-and-post (PRD 5 US-7/8, 10.1, 12.2, 12.3).

Nothing is ever posted to GitHub except through `approve` (S8). Posting is exactly-once:
an atomic claim stops double-clicks and two admins, a hidden marker lets a retry find an issue
that a timed-out request actually created, and the POST itself is never auto-retried.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import AppError
from app.core.text import truncate
from app.db import utcnow
from app.github.client import GitHubClient
from app.github.errors import (
    GitHubError,
    GitHubRateLimited,
    GitHubUnavailable,
)
from app.github.repo_meta import parse_repo_payload
from app.models.issue_draft import DraftStatus, IssueDraft
from app.models.repo import Repo
from app.models.scan import RuleStatus, Scan, ScanResult, ScanStatus
from app.models.user import User
from app.rules.registry import get_rule
from app.scanner.deadline import Deadline, ScanTimeout
from app.schemas.issue_draft import IssueDraftOut
from app.services.issue_body import DraftItem, build_body, build_title

logger = logging.getLogger("gitwell.issues")

OPEN_STATUSES = (DraftStatus.drafted, DraftStatus.approved, DraftStatus.posting)
POST_TIMEOUT_SECONDS = 30
LAST_ERROR_LIMIT = 500

MSG_ISSUES_UNAVAILABLE = "Issues are disabled for this repository, or it is archived."
MSG_NO_PERMISSION = "The GitHub token is not allowed to create issues in this repository."


def marker(draft_id: int) -> str:
    """Hidden dedupe marker, appended at post time. It is never part of the editable body."""
    return f"<!-- gitwell-draft:{draft_id} -->"


def to_out(draft: IssueDraft, repo: Repo) -> IssueDraftOut:
    return IssueDraftOut(
        id=draft.id,
        scan_id=draft.scan_id,
        repo_id=draft.repo_id,
        repo_full_name=repo.full_name,
        repo_is_private=repo.is_private,
        title=draft.title,
        body=draft.body,
        status=draft.status,
        post_attempts=draft.post_attempts,
        github_issue_number=draft.github_issue_number,
        github_issue_url=draft.github_issue_url,
        last_error=draft.last_error,
        created_by_id=draft.created_by_id,
        approved_by_id=draft.approved_by_id,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        approved_at=draft.approved_at,
    )


def _not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Issue draft not found.")


def _invalid_state(status: DraftStatus) -> AppError:
    return AppError(
        409,
        "INVALID_STATE",
        f"This draft is {status.value} and cannot be changed that way.",
        {"status": status.value},
    )


def _load(db: Session, draft_id: int) -> tuple[IssueDraft, Repo]:
    draft = db.get(IssueDraft, draft_id)
    if draft is None:
        raise _not_found()
    repo = db.get(Repo, draft.repo_id)
    if repo is None:  # cascade makes this unreachable; be defensive
        raise _not_found()
    return draft, repo


def get_draft(db: Session, draft_id: int) -> IssueDraftOut:
    db.expire_all()
    return to_out(*_load(db, draft_id))


def list_drafts(
    db: Session, repo_id: int | None, status: DraftStatus | None, limit: int, offset: int
) -> tuple[list[IssueDraftOut], int]:
    conditions: list[Any] = []
    if repo_id is not None:
        conditions.append(IssueDraft.repo_id == repo_id)
    if status is not None:
        conditions.append(IssueDraft.status == status)
    total = db.scalar(select(func.count()).select_from(IssueDraft).where(*conditions)) or 0
    rows = db.execute(
        select(IssueDraft, Repo)
        .join(Repo, Repo.id == IssueDraft.repo_id)
        .where(*conditions)
        .order_by(IssueDraft.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [to_out(draft, repo) for draft, repo in rows], total


# ---- create ----------------------------------------------------------------------------------


def _draft_exists(draft_id: int | None) -> AppError:
    return AppError(
        409,
        "DRAFT_EXISTS",
        "This repository already has an open issue draft.",
        {"draft_id": draft_id},
    )


def create_draft(db: Session, user: User, scan_id: int) -> IssueDraftOut:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise AppError(404, "NOT_FOUND", "Scan not found.")
    if scan.status != ScanStatus.completed:
        raise AppError(
            409, "INVALID_STATE", "Only a completed scan can be turned into an issue draft."
        )
    repo = db.get(Repo, scan.repo_id)
    if repo is None:
        raise AppError(404, "NOT_FOUND", "Repository not found.")

    # Checks run in the documented order.
    open_id = db.scalar(
        select(IssueDraft.id).where(
            IssueDraft.repo_id == repo.id, IssueDraft.status.in_(OPEN_STATUSES)
        )
    )
    if open_id is not None:
        raise _draft_exists(open_id)
    if repo.is_archived or not repo.has_issues:
        raise AppError(422, "ISSUES_UNAVAILABLE", MSG_ISSUES_UNAVAILABLE)

    rows = db.scalars(
        select(ScanResult)
        .where(
            ScanResult.scan_id == scan.id,
            ScanResult.status.in_((RuleStatus.fail, RuleStatus.warn)),
        )
        .order_by(ScanResult.display_order, ScanResult.rule_id)
    ).all()
    if not rows:
        raise AppError(422, "NOTHING_TO_REPORT", "This scan has no failing or warning checks.")

    items = []
    for row in rows:
        rule = get_rule(row.rule_id)
        items.append(
            DraftItem(
                rule_id=row.rule_id,
                name=rule.name if rule else row.rule_id,
                status=RuleStatus(row.status).value,
                message=row.message,
                fix_tip=rule.fix_tip if rule else "",
            )
        )
    scanned_on = (scan.finished_at or scan.created_at).astimezone(UTC).date()
    draft = IssueDraft(
        scan_id=scan.id,
        repo_id=repo.id,
        title=build_title(len(items), scan.score),
        body=build_body(
            items,
            commit_sha=scan.commit_sha,
            scanned_on=scanned_on,
            score=scan.score,
            grade=scan.grade,
        ),
        status=DraftStatus.drafted,
        created_by_id=user.id,
    )
    db.add(draft)
    try:
        db.commit()
    except IntegrityError:
        # Two admins raced: the partial unique index let only one open draft through.
        db.rollback()
        open_id = db.scalar(
            select(IssueDraft.id).where(
                IssueDraft.repo_id == repo.id, IssueDraft.status.in_(OPEN_STATUSES)
            )
        )
        if open_id is None:
            raise AppError(404, "NOT_FOUND", "Scan not found.") from None
        raise _draft_exists(open_id) from None
    return to_out(draft, repo)


# ---- edit and discard -----------------------------------------------------------------------


def update_draft(db: Session, draft_id: int, title: str | None, body: str | None) -> IssueDraftOut:
    draft, repo = _load(db, draft_id)
    if draft.status != DraftStatus.drafted:
        raise _invalid_state(draft.status)
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = title
    if body is not None:
        values["body"] = body
    if not values:
        return to_out(draft, repo)
    changed = db.execute(
        update(IssueDraft)
        .where(IssueDraft.id == draft_id, IssueDraft.status == DraftStatus.drafted)
        .values(**values, updated_at=utcnow())
    )
    db.commit()
    if not getattr(changed, "rowcount", 0):  # approved/discarded in the meantime
        db.expire_all()
        raise _invalid_state(_load(db, draft_id)[0].status)
    return get_draft(db, draft_id)


def discard_draft(db: Session, draft_id: int) -> IssueDraftOut:
    draft, _ = _load(db, draft_id)
    if draft.status not in (DraftStatus.drafted, DraftStatus.approved):
        raise _invalid_state(draft.status)
    changed = db.execute(
        update(IssueDraft)
        .where(
            IssueDraft.id == draft_id,
            IssueDraft.status.in_((DraftStatus.drafted, DraftStatus.approved)),
        )
        .values(status=DraftStatus.discarded, updated_at=utcnow())
    )
    db.commit()
    if not getattr(changed, "rowcount", 0):  # e.g. an approve claimed it first
        db.expire_all()
        raise _invalid_state(_load(db, draft_id)[0].status)
    return get_draft(db, draft_id)


# ---- approve and post (PRD 12.3) -----------------------------------------------------------


def _set(db: Session, draft_id: int, expected: DraftStatus, **values: Any) -> bool:
    result = db.execute(
        update(IssueDraft)
        .where(IssueDraft.id == draft_id, IssueDraft.status == expected)
        .values(**values, updated_at=utcnow())
    )
    db.commit()
    return bool(getattr(result, "rowcount", 0))


def _release(db: Session, draft_id: int, message: str) -> None:
    """Back to `approved` with the reason; a retry is safe (see step 4)."""
    db.rollback()
    _set(
        db,
        draft_id,
        DraftStatus.posting,
        status=DraftStatus.approved,
        last_error=truncate(message, LAST_ERROR_LIMIT),
    )


def _mark_posted(db: Session, draft_id: int, number: int | None, url: str | None) -> None:
    db.rollback()
    ok = _set(
        db,
        draft_id,
        DraftStatus.posting,
        status=DraftStatus.posted,
        github_issue_number=number,
        github_issue_url=truncate(url, 300) if url else None,
        last_error=None,
    )
    logger.info(
        "issue_posted" if ok else "issue_posted_but_draft_not_posting",
        extra={"draft_id": draft_id, "issue_number": number},
    )


def _bump_attempts(db: Session, draft_id: int) -> None:
    """Incremented and COMMITTED right before the POST, so a crash still counts the attempt."""
    db.rollback()
    db.execute(
        update(IssueDraft)
        .where(IssueDraft.id == draft_id)
        .values(post_attempts=IssueDraft.post_attempts + 1, updated_at=utcnow())
    )
    db.commit()


def _issue_fields(data: Any, owner: str, name: str) -> tuple[int | None, str | None]:
    if not isinstance(data, dict):
        return None, None
    number = data.get("number")
    number = number if isinstance(number, int) and not isinstance(number, bool) else None
    url = data.get("html_url")
    if not isinstance(url, str) or not url.startswith("https://github.com/"):
        url = f"https://github.com/{owner}/{name}/issues/{number}" if number else None
    return number, url


def _post_failure(resp: httpx.Response, client: GitHubClient) -> Exception:
    """Map a non-201 answer to the PRD's errors. Messages are fixed, never response bodies."""
    if resp.status_code == 410:
        return AppError(422, "ISSUES_UNAVAILABLE", MSG_ISSUES_UNAVAILABLE)
    error = client.classify(resp, repo_lookup=False)
    if isinstance(error, GitHubRateLimited | GitHubUnavailable):
        return error
    if resp.status_code in (403, 404, 451):
        return AppError(422, "NO_REPO_PERMISSION", MSG_NO_PERMISSION)
    return GitHubError({"status": resp.status_code})


def _find_marked_issue(
    client: GitHubClient, owner: str, name: str, since: datetime, draft_id: int, deadline: Deadline
) -> tuple[int | None, str | None] | None:
    wanted = marker(draft_id)
    for issue in client.list_issues_since(owner, name, since, deadline):
        body = issue.get("body")
        if isinstance(body, str) and wanted in body:
            return _issue_fields(issue, owner, name)
    return None


def _post_flow(
    db: Session,
    client: GitHubClient,
    draft_id: int,
    github_id: int,
    title: str,
    body: str,
    post_attempts: int,
    approved_at: datetime | None,
    deadline: Deadline,
) -> None:
    """Steps 3-6. Raises on any failure; the caller releases the claim."""
    raw = client.get_repo_by_id(github_id, deadline)
    meta = parse_repo_payload(raw)
    db.execute(
        update(Repo)
        .where(Repo.github_id == github_id)
        .values(
            is_archived=meta.is_archived, has_issues=meta.has_issues, is_private=meta.is_private
        )
    )
    db.commit()
    if meta.is_archived or not meta.has_issues:
        raise AppError(422, "ISSUES_UNAVAILABLE", MSG_ISSUES_UNAVAILABLE)
    permissions = raw.get("permissions")
    if (
        isinstance(permissions, dict)
        and not permissions.get("push")
        and not permissions.get("triage")
    ):
        raise AppError(422, "NO_REPO_PERMISSION", MSG_NO_PERMISSION)

    # Step 4: an earlier attempt may have succeeded (timeout, crash). Adopt it instead of
    # posting again. If this lookup fails we stop: posting blind could create a duplicate.
    if post_attempts > 0 and approved_at is not None:
        existing = _find_marked_issue(
            client, meta.owner, meta.name, approved_at, draft_id, deadline
        )
        if existing is not None:
            _mark_posted(db, draft_id, *existing)
            return

    _bump_attempts(db, draft_id)
    response = client.post_issue(
        meta.owner, meta.name, title, f"{body}\n\n{marker(draft_id)}", deadline
    )
    if response.status_code == 201:
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        _mark_posted(db, draft_id, *_issue_fields(payload, meta.owner, meta.name))
        return
    raise _post_failure(response, client)


def approve(
    db: Session, settings: Settings, client: GitHubClient, draft_id: int, user: User
) -> IssueDraftOut:
    """Approve and post. Exactly once, even with double-clicks, two admins, timeouts or crashes."""
    _load(db, draft_id)  # 404 if missing
    # Step 1: no state change without a token.
    if not (settings.github_issues_token or settings.github_token):
        raise AppError(
            422, "GITHUB_TOKEN_REQUIRED", "A GitHub token must be configured to post issues."
        )

    # Step 2: atomic claim. Only one caller can move drafted/approved -> posting.
    now = utcnow()
    claim = db.execute(
        update(IssueDraft)
        .where(
            IssueDraft.id == draft_id,
            IssueDraft.status.in_((DraftStatus.drafted, DraftStatus.approved)),
        )
        .values(
            status=DraftStatus.posting,
            approved_by_id=user.id,
            approved_at=func.coalesce(IssueDraft.approved_at, now),
            updated_at=now,
        )
    )
    db.commit()
    if not getattr(claim, "rowcount", 0):
        db.expire_all()
        raise _invalid_state(_load(db, draft_id)[0].status)

    # Read what the flow needs into plain values, then end the transaction: no database
    # transaction is open while GitHub is being called.
    row = db.execute(
        select(
            IssueDraft.title,
            IssueDraft.body,
            IssueDraft.post_attempts,
            IssueDraft.approved_at,
            Repo.github_id,
        )
        .join(Repo, Repo.id == IssueDraft.repo_id)
        .where(IssueDraft.id == draft_id)
    ).one()
    db.rollback()

    try:
        _post_flow(
            db,
            client,
            draft_id,
            row.github_id,
            row.title,
            row.body,
            row.post_attempts,
            row.approved_at,
            Deadline(POST_TIMEOUT_SECONDS),
        )
    except ScanTimeout:
        _release(db, draft_id, GitHubUnavailable.message)
        raise GitHubUnavailable from None
    except AppError as exc:
        _release(db, draft_id, exc.message)
        raise
    except GitHubError as exc:
        _release(db, draft_id, exc.message)
        raise
    except Exception:
        # Never leave a draft stuck in `posting`; a retry will look for an already-created issue.
        _release(db, draft_id, "Unexpected internal error.")
        raise
    return get_draft(db, draft_id)
