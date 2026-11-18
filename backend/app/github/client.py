"""GitHub REST client (PRD 8.2, 12, S5, S6).

- Only talks to the configured API host and, for tarballs, to ``codeload.github.com``.
- Never follows redirects automatically; every hop is validated.
- Never logs URLs (they can carry short-lived tokens); logs route templates only.
- The Authorization header is never sent to codeload.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.core.metrics import get_metrics
from app.github.errors import (
    GitHubEmptyRepo,
    GitHubError,
    GitHubNotFound,
    GitHubRateLimited,
    GitHubUnavailable,
)
from app.github.ratelimit_state import RateLimitState, rate_limit_state
from app.scanner.deadline import Deadline

logger = logging.getLogger("gitwell.github")

API_VERSION = "2022-11-28"
USER_AGENT = "GitWell/1.0"
CODELOAD_HOST = "codeload.github.com"
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 20.0
TARBALL_READ_TIMEOUT = 60.0
MAX_RETRIES = 2
BACKOFFS = (1.0, 2.0)
MAX_API_REDIRECTS = 3
CHUNK_SIZE = 64 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRY_STATUSES = frozenset({502, 503, 504})


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return (parts.scheme.lower(), (parts.hostname or "").lower(), parts.port)


class GitHubClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        state: RateLimitState | None = None,
    ) -> None:
        # httpx logs full URLs at INFO, including codeload ?token= values (S6).
        for name in ("httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)
        self._base = base_url.rstrip("/")
        self._base_origin = _origin(self._base)
        self._token = token or None
        self._sleep = sleep
        self._state = state if state is not None else rate_limit_state
        self._http = httpx.Client(transport=transport, follow_redirects=False)

    # ---- lifecycle -----------------------------------------------------------------

    @property
    def has_token(self) -> bool:
        return self._token is not None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- low-level -----------------------------------------------------------------

    def _headers(self, accept: str | None, auth: bool) -> dict[str, str]:
        headers = {
            "Accept": accept or "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _timeout(self, deadline: Deadline, read: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=deadline.clamp(CONNECT_TIMEOUT),
            read=deadline.clamp(read),
            write=deadline.clamp(READ_TIMEOUT),
            pool=deadline.clamp(CONNECT_TIMEOUT),
        )

    def _backoff(self, attempt: int, deadline: Deadline) -> None:
        self._sleep(min(BACKOFFS[attempt], max(deadline.remaining(), 0.0)))
        deadline.check()

    def _send(
        self,
        method: str,
        url: str,
        *,
        endpoint: str,
        deadline: Deadline,
        accept: str | None = None,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        retry: bool = True,
    ) -> httpx.Response:
        """One logical request with retries on network errors and 502/503/504."""
        if _origin(url)[:2] != self._base_origin[:2]:
            raise GitHubError({"reason": "host_not_allowed"})
        for attempt in range(MAX_RETRIES + 1):
            deadline.check()
            try:
                resp = self._http.request(
                    method,
                    url,
                    headers=self._headers(accept, auth=True),
                    params=params,
                    json=json,
                    timeout=self._timeout(deadline, READ_TIMEOUT),
                )
            except httpx.HTTPError:
                get_metrics().github_requests.labels(endpoint, "error").inc()
                if retry and attempt < MAX_RETRIES:
                    self._backoff(attempt, deadline)
                    continue
                deadline.check()  # a timeout caused by the deadline is SCAN_TIMEOUT
                raise GitHubUnavailable from None
            self._state.update(resp.headers)
            get_metrics().github_requests.labels(endpoint, str(resp.status_code)).inc()
            logger.debug("github_request", extra={"endpoint": endpoint, "status": resp.status_code})
            if resp.status_code in _RETRY_STATUSES and retry and attempt < MAX_RETRIES:
                self._backoff(attempt, deadline)
                continue
            return resp
        raise GitHubUnavailable  # pragma: no cover - loop always returns or raises

    def _validated_api_redirect(self, current: str, resp: httpx.Response) -> str:
        location = resp.headers.get("location")
        if not location:
            raise GitHubError({"reason": "redirect_without_location"})
        target = str(urljoin(current, location))
        if _origin(target)[:2] != self._base_origin[:2]:
            raise GitHubError({"reason": "redirect_host_not_allowed"})
        return target

    def request(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        deadline: Deadline,
        accept: str | None = None,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        retry: bool = True,
    ) -> httpx.Response:
        """Request an API path, following redirects to the same API host only (max 3)."""
        url = self._base + path
        for hop in range(MAX_API_REDIRECTS + 1):
            resp = self._send(
                method,
                url,
                endpoint=endpoint,
                deadline=deadline,
                accept=accept,
                params=params,
                json=json,
                retry=retry,
            )
            if resp.status_code in _REDIRECT_STATUSES:
                if hop == MAX_API_REDIRECTS:
                    raise GitHubError({"reason": "too_many_redirects"})
                url = self._validated_api_redirect(url, resp)
                if "?" in url:
                    params = None
                continue
            return resp
        raise GitHubError  # pragma: no cover

    # ---- error classification (PRD 12) ----------------------------------------------

    def classify(self, resp: httpx.Response, *, repo_lookup: bool = True) -> GitHubError:
        status = resp.status_code
        headers = resp.headers
        now = datetime.now(UTC)
        if status in (403, 429):
            retry_after = headers.get("retry-after")
            if retry_after is not None:
                try:
                    seconds = int(retry_after)
                except ValueError:
                    seconds = 60
                return GitHubRateLimited(now + timedelta(seconds=seconds))
            if headers.get("x-ratelimit-remaining") == "0":
                try:
                    reset = datetime.fromtimestamp(int(headers.get("x-ratelimit-reset", "")), UTC)
                except (ValueError, OverflowError, OSError):
                    reset = now + timedelta(seconds=60)
                return GitHubRateLimited(reset)
            if "secondary rate limit" in _body_message(resp).lower():
                return GitHubRateLimited(now + timedelta(seconds=60))
            if status == 403 and "x-github-sso" in headers:
                return GitHubNotFound({"reason": "sso_required"})
        if status in (404, 451) or (status == 403 and repo_lookup):
            return GitHubNotFound()
        if status >= 500:
            return GitHubUnavailable()
        return GitHubError({"status": status})

    def _json(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            raise GitHubError({"reason": "invalid_json"}) from None

    # ---- endpoints -------------------------------------------------------------------

    def get_repo_by_id(self, github_id: int, deadline: Deadline) -> dict[str, Any]:
        resp = self.request(
            "GET",
            f"/repositories/{int(github_id)}",
            endpoint="/repositories/{id}",
            deadline=deadline,
        )
        if resp.status_code != 200:
            raise self.classify(resp)
        data = self._json(resp)
        if not isinstance(data, dict):
            raise GitHubError({"reason": "unexpected_shape"})
        return data

    def get_repo(self, owner: str, name: str, deadline: Deadline) -> dict[str, Any]:
        resp = self.request(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}",
            endpoint="/repos/{owner}/{repo}",
            deadline=deadline,
        )
        if resp.status_code != 200:
            raise self.classify(resp)
        data = self._json(resp)
        if not isinstance(data, dict):
            raise GitHubError({"reason": "unexpected_shape"})
        return data

    def get_commit_sha(self, owner: str, name: str, branch: str, deadline: Deadline) -> str:
        resp = self.request(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/commits/{quote(branch, safe='')}",
            endpoint="/repos/{owner}/{repo}/commits/{ref}",
            deadline=deadline,
            accept="application/vnd.github.sha",
        )
        if resp.status_code == 409:
            raise GitHubEmptyRepo
        if resp.status_code != 200:
            raise self.classify(resp)
        sha = resp.text.strip()
        if not sha or len(sha) > 64 or not all(c in "0123456789abcdefABCDEF" for c in sha):
            raise GitHubError({"reason": "unexpected_sha"})
        return sha.lower()

    def get_tree(self, owner: str, name: str, sha: str, deadline: Deadline) -> dict[str, Any]:
        resp = self.request(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/git/trees/{quote(sha, safe='')}",
            endpoint="/repos/{owner}/{repo}/git/trees/{sha}",
            deadline=deadline,
            params={"recursive": "1"},
        )
        if resp.status_code != 200:
            raise self.classify(resp)
        data = self._json(resp)
        if not isinstance(data, dict):
            raise GitHubError({"reason": "unexpected_shape"})
        return data

    def get_file_raw(self, owner: str, name: str, path: str, sha: str, deadline: Deadline) -> bytes:
        resp = self.request(
            "GET",
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/contents/{quote(path, safe='/')}",
            endpoint="/repos/{owner}/{repo}/contents/{path}",
            deadline=deadline,
            accept="application/vnd.github.raw+json",
            params={"ref": sha},
        )
        if resp.status_code != 200:
            raise self.classify(resp)
        return resp.content

    # ---- issues (PRD 12.3) -----------------------------------------------------------

    def list_issues_since(
        self, owner: str, name: str, since: datetime, deadline: Deadline, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        """Issues (and PRs) updated since `since`, any state, newest first (up to 100 per page)."""
        found: list[dict[str, Any]] = []
        stamp = since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for page in range(1, max_pages + 1):
            resp = self.request(
                "GET",
                f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/issues",
                endpoint="/repos/{owner}/{repo}/issues",
                deadline=deadline,
                params={"state": "all", "since": stamp, "per_page": "100", "page": str(page)},
            )
            if resp.status_code != 200:
                raise self.classify(resp)
            data = self._json(resp)
            if not isinstance(data, list):
                raise GitHubError({"reason": "unexpected_shape"})
            found.extend(item for item in data if isinstance(item, dict))
            if len(data) < 100:
                break
        return found

    def post_issue(
        self, owner: str, name: str, title: str, body: str, deadline: Deadline
    ) -> httpx.Response:
        """Create an issue. NEVER auto-retried: a retry could create a duplicate (PRD 12)."""
        return self.request(
            "POST",
            f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}/issues",
            endpoint="/repos/{owner}/{repo}/issues",
            deadline=deadline,
            json={"title": title, "body": body},
            retry=False,
        )

    # ---- tarball (PRD 8.2) -----------------------------------------------------------

    def stream_tarball(
        self, owner: str, name: str, sha: str, deadline: Deadline
    ) -> Iterator[bytes]:
        """Return an iterator over the repository tarball.

        The API call happens now (so errors raise here); chunks are read lazily. The API
        answers 302 with a codeload URL. That URL is validated (https and host exactly
        ``codeload.github.com``), then fetched WITHOUT the Authorization header.
        """
        url = (
            f"{self._base}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
            f"/tarball/{quote(sha, safe='')}"
        )
        resp = self._send(
            "GET", url, endpoint="/repos/{owner}/{repo}/tarball/{ref}", deadline=deadline
        )
        if resp.status_code in _REDIRECT_STATUSES:
            return self._codeload_chunks(self._validated_codeload(url, resp), deadline)
        if resp.status_code >= 400:
            raise self.classify(resp)
        raise GitHubError({"reason": "expected_redirect"})

    @staticmethod
    def _validated_codeload(current: str, resp: httpx.Response) -> str:
        location = resp.headers.get("location")
        if not location:
            raise GitHubError({"reason": "redirect_without_location"})
        target = str(urljoin(current, location))
        parts = urlsplit(target)
        if (
            parts.scheme != "https"
            or parts.hostname != CODELOAD_HOST
            or parts.username is not None
            or parts.port not in (None, 443)
        ):
            raise GitHubError({"reason": "redirect_host_not_allowed"})
        return target

    def _codeload_chunks(self, url: str, deadline: Deadline) -> Iterator[bytes]:
        headers = {"User-Agent": USER_AGENT}  # no Authorization: codeload has its own token
        for hop in range(2):  # the validated Location, plus at most one more redirect
            deadline.check()
            try:
                with self._http.stream(
                    "GET",
                    url,
                    headers=headers,
                    timeout=self._timeout(deadline, TARBALL_READ_TIMEOUT),
                ) as resp:
                    if resp.status_code in _REDIRECT_STATUSES:
                        if hop == 1:
                            raise GitHubError({"reason": "too_many_redirects"})
                        url = self._validated_codeload(url, resp)
                        continue
                    if resp.status_code in (404, 410):
                        raise GitHubNotFound
                    if resp.status_code >= 500:
                        raise GitHubUnavailable
                    if resp.status_code != 200:
                        raise GitHubError({"status": resp.status_code})
                    get_metrics().github_requests.labels("codeload tarball", "200").inc()
                    logger.debug("github_request", extra={"endpoint": "codeload tarball"})
                    for chunk in resp.iter_bytes(CHUNK_SIZE):
                        deadline.check()
                        yield chunk
                    return
            except httpx.HTTPError:
                deadline.check()
                raise GitHubUnavailable from None
        raise GitHubError({"reason": "too_many_redirects"})  # pragma: no cover


def _body_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str):
            return message
    return ""
