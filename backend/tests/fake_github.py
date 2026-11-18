"""A stateful fake of the parts of GitHub that GitWell talks to, served through respx."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import httpx
import respx

from tests.fakes import make_tarball

API = "https://api.github.com"
CODELOAD = "https://codeload.github.com"

# A bare repo: only `secrets` passes and `dependencies` is not applicable -> score 22 (grade F).
BARE_FILES: dict[str, str] = {"main.py": "print('hi')"}
# Every rule passes -> score 100 (grade A).
GOOD_FILES: dict[str, str] = {
    "README.md": "# Project\n\n" + "This project does many useful things. " * 10,
    "LICENSE": "MIT License",
    "CONTRIBUTING.md": "Please contribute.",
    ".gitignore": ".env\n.env.*\n",
    ".env.example": "A=1",
    ".github/workflows/ci.yml": "name: CI\non: push\n",
    "tests/test_app.py": "def test_x(): pass",
    "src/app.py": "print('hi')",
    "pyproject.toml": '[project]\nname = "x"\ndependencies = ["flask==3.0.0"]\n',
}


@dataclass
class FakeRepo:
    github_id: int
    owner: str
    name: str
    default_branch: str = "main"
    private: bool = False
    archived: bool = False
    has_issues: bool = True
    disabled: bool = False
    size_kb: int = 100
    files: Mapping[str, str | bytes] = field(default_factory=lambda: dict(BARE_FILES))
    sha: str = "a" * 40
    empty: bool = False  # no commits (409 on the commit lookup)
    rate_limited: bool = False
    gone: bool = False  # 404 everywhere
    aliases: set[tuple[str, str]] = field(default_factory=set)  # old (owner, name), lowercased
    on_commit_lookup: Callable[[], None] | None = None  # hook, called mid-scan
    # issues API
    permissions: dict[str, bool] = field(
        default_factory=lambda: {"admin": False, "push": True, "triage": True, "pull": True}
    )
    issues: list[dict[str, object]] = field(default_factory=list)
    # How POST /issues behaves: "ok", "timeout_after_create" (GitHub created it but the response
    # was lost), "timeout_before_create", "error_after_create" (502 after creating), or an HTTP
    # status int (nothing created).
    post_mode: str | int = "ok"
    on_post: Callable[[], None] | None = None  # hook, called when a POST /issues arrives
    on_repo_lookup: Callable[[], None] | None = None  # hook, called on GET /repositories/{id}
    list_status: int | None = None  # make GET /issues answer with this HTTP status

    def payload(self) -> dict[str, object]:
        return {
            "id": self.github_id,
            "name": self.name,
            "owner": {"login": self.owner},
            "default_branch": self.default_branch,
            "private": self.private,
            "archived": self.archived,
            "has_issues": self.has_issues,
            "disabled": self.disabled,
            "size": self.size_kb,
            "permissions": self.permissions,
        }

    def create_issue(self, title: str, body: str) -> dict[str, object]:
        number = len(self.issues) + 1
        issue = {
            "number": number,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{self.owner}/{self.name}/issues/{number}",
        }
        self.issues.append(issue)
        return issue

    def rename(self, new_name: str) -> None:
        self.aliases.add((self.owner.lower(), self.name.lower()))
        self.name = new_name


class FakeGitHub:
    def __init__(self, router: respx.MockRouter) -> None:
        self.repos: dict[int, FakeRepo] = {}
        self.calls: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, str | None]] = []  # method, path, Authorization
        self.repo = self.add_repo("o", "r", 42)
        router.route(host="api.github.com").mock(side_effect=self._api)
        router.route(host="codeload.github.com").mock(side_effect=self._codeload)

    def posts(self) -> int:
        """How many POST /issues requests GitHub received."""
        return sum(1 for m, p in self.calls if m == "POST" and p.endswith("/issues"))

    def add_repo(self, owner: str, name: str, github_id: int, **kw: object) -> FakeRepo:
        repo = FakeRepo(github_id, owner, name, **kw)  # type: ignore[arg-type]
        self.repos[github_id] = repo
        return repo

    # ---- lookup helpers ---------------------------------------------------------------------

    def _by_name(self, owner: str, name: str) -> tuple[FakeRepo | None, bool]:
        key = (owner.lower(), name.lower())
        for repo in self.repos.values():
            if (repo.owner.lower(), repo.name.lower()) == key:
                return repo, False
            if key in repo.aliases:
                return repo, True
        return None, False

    @staticmethod
    def _blocked(repo: FakeRepo) -> httpx.Response | None:
        if repo.gone:
            return httpx.Response(404, json={"message": "Not Found"})
        if repo.rate_limited:
            return httpx.Response(
                403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795000000"}
            )
        return None

    # ---- handlers ---------------------------------------------------------------------------

    def _api(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        self.requests.append((request.method, path, request.headers.get("authorization")))

        if m := re.fullmatch(r"/repositories/(\d+)", path):
            repo = self.repos.get(int(m.group(1)))
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if repo.on_repo_lookup:
                repo.on_repo_lookup()
            return self._blocked(repo) or httpx.Response(200, json=repo.payload())

        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)/issues", path):
            return self._issues(request, m.group(1), m.group(2))

        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)", path):
            repo, renamed = self._by_name(m.group(1), m.group(2))
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if blocked := self._blocked(repo):
                return blocked
            if renamed:
                return httpx.Response(
                    301, headers={"Location": f"{API}/repositories/{repo.github_id}"}
                )
            return httpx.Response(200, json=repo.payload())

        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)/commits/(.+)", path):
            repo, _ = self._by_name(m.group(1), m.group(2))
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            if blocked := self._blocked(repo):
                return blocked
            if repo.on_commit_lookup:
                repo.on_commit_lookup()
            if repo.empty:
                return httpx.Response(409, json={"message": "Git Repository is empty."})
            return httpx.Response(200, text=repo.sha)

        if m := re.fullmatch(r"/repos/([^/]+)/([^/]+)/tarball/(.+)", path):
            repo, _ = self._by_name(m.group(1), m.group(2))
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(
                302,
                headers={
                    "Location": f"{CODELOAD}/{repo.owner}/{repo.name}/legacy.tar.gz/"
                    f"{m.group(3)}?token=SHORTLIVED"
                },
            )

        return httpx.Response(404, json={"message": "Not Found"})

    def _issues(self, request: httpx.Request, owner: str, name: str) -> httpx.Response:
        repo, _ = self._by_name(owner, name)
        if repo is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if blocked := self._blocked(repo):
            return blocked
        if request.method == "GET":
            if repo.list_status:
                return httpx.Response(repo.list_status, json={"message": "boom"})
            per_page = int(request.url.params.get("per_page", "30"))
            page = int(request.url.params.get("page", "1"))
            start = (page - 1) * per_page
            return httpx.Response(200, json=repo.issues[start : start + per_page])

        # POST: create an issue
        if repo.on_post:
            repo.on_post()
        sent = json.loads(request.content)
        mode = repo.post_mode
        if mode == "timeout_before_create":
            raise httpx.ReadTimeout("no response", request=request)
        if mode in ("ok", "timeout_after_create", "error_after_create"):
            issue = repo.create_issue(sent["title"], sent["body"])
            if mode == "timeout_after_create":
                raise httpx.ReadTimeout("response lost", request=request)
            if mode == "error_after_create":
                return httpx.Response(502, json={"message": "Bad Gateway"})
            return httpx.Response(201, json=issue)
        if mode == "rate_limited":
            return httpx.Response(
                403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1795000000"}
            )
        assert isinstance(mode, int)
        return httpx.Response(mode, json={"message": "secret internal GitHub message"})

    def _codeload(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        parts = request.url.path.strip("/").split("/")
        repo, _ = self._by_name(parts[0], parts[1])
        if repo is None:
            return httpx.Response(404)
        return httpx.Response(200, content=make_tarball(repo.files))
