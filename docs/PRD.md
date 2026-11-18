# GitWell — Product Requirements Document

| Field | Value |
|---|---|
| **Product** | GitWell |
| **Doc type** | PRD + technical specification (build-ready) |
| **Version** | 2.0 (final, after implementer + architecture review) |
| **Date** | 2026-11-18 |
| **Status** | Approved for build |
| **Audience** | Engineers (human or AI) who will implement GitWell from this document alone |

---

## 0. How to use this document (read first if you are implementing)

1. This document is the **single source of truth**. If something is not specified, choose the simplest option that fits the rest of the spec and record it in `docs/DECISIONS.md`.
2. Build in the **milestone order in §17**. Each milestone has acceptance criteria. Do not start the next milestone until the current one's criteria pass and its tests are green.
3. Names in `code font` are **contracts**: tables, columns, endpoints, env vars, rule IDs, error codes. Use them exactly.
4. MUST = required. SHOULD = strongly recommended. MAY = optional.
5. **No real network calls in tests.** Every GitHub call is mocked.
6. Regexes and enum value lists appear **only in fenced code blocks**, never in tables, so markdown escaping can never corrupt them. Copy them verbatim.

---

## 1. Summary

GitWell scans GitHub repositories against an engineering-quality checklist: README, license, tests, CI, committed secrets, pinned dependencies, `.gitignore` and contributor docs. Each repo gets a **0–100 score**, a per-rule breakdown and a concrete fix for every failure. Scans are stored, so teams can watch scores change over time on a dashboard. For failing repos, GitWell drafts a GitHub issue that an admin reviews, edits and approves. **Nothing is ever posted to GitHub without explicit human approval.**

## 2. Problem

Repositories drift apart. One has no tests, another has no README, a third has an API key committed by mistake, and a fourth has no CI. Checking each one by hand is slow, inconsistent and never repeated. Nobody has a shared view of "how healthy are our repos, and are they getting better?"

## 3. Goals and non-goals

### 3.1 Goals (v1)
- **G1:** Scan any public GitHub repo, and private repos the configured token can access, given its URL.
- **G2:** Apply a pluggable rule set. Each rule returns `pass`, `warn` or `fail` with an explanation and a fix tip.
- **G3:** Produce a **deterministic** 0–100 score. The same commit plus the same rule set always gives the same result, on any OS.
- **G4:** Store every scan and show score history per repo.
- **G5:** Provide a dashboard with all repos side by side, trends, and the most common failures.
- **G6:** Draft GitHub issues for failures. Posting requires human approval, and each approved draft is posted **exactly once**.
- **G7:** Re-scan all repos on a schedule.
- **G8:** Adding a new rule is a **one-source-file change**, plus its test file.

### 3.2 Non-goals (v1). Do NOT build these
- GitLab, Bitbucket, GitHub Enterprise Server, or any host other than github.com.
- Auto-fixing repos or opening pull requests.
- Auto-discovering all repos in a GitHub org. Repos are added one at a time.
- GitHub webhooks or scan-on-push.
- "Sign in with GitHub" (OAuth), email verification, password reset by email, 2FA.
- Multi-tenancy or teams. All users share one global repo list.
- Per-repo or per-user rule configuration or weights.
- Notifications (email, Slack).
- Scanning git **history** for secrets. Only the default branch's current tree is scanned, and the UI says so.
- Running more than one backend process (see §6).

## 4. Users and roles

| Role | Who | Can |
|---|---|---|
| `viewer` | Engineers and managers who want visibility | Log in; view repos, scans, results, history, dashboard and issue drafts; change own password |
| `admin` | Tech lead or platform owner | Everything a viewer can, plus: create users, add and delete repos, trigger scans, create, edit, discard and approve issue drafts, manage user roles and active status |

Rules:
- **Self-registration is off by default** (`ALLOW_REGISTRATION=false`). Admins create users with `POST /users`. If registration is enabled, it always creates a `viewer`.
- **Bootstrap admin:** at startup, if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are both set and no user has that email, create that user as an admin. If the user already exists, never modify it.
  - If only one of the two variables is set, log a warning and skip.
  - If `ADMIN_PASSWORD` is shorter than 8 characters, fail startup.
- **Lockout protection:** any change that would leave **zero active admins** is rejected with 409 `LAST_ADMIN`. That covers demoting or deactivating the last admin, and also self-deactivation. Admins cannot change their own role or active flag (409 `LAST_ADMIN` if they are the last admin, otherwise 409 `INVALID_STATE`).

## 5. User stories and acceptance criteria

| ID | Story | Acceptance criteria |
|---|---|---|
| US-1 | As an admin, I add a repo by pasting its URL | Valid URL → 201, repo created, and an `on_add` scan queued. Invalid URL → 422 `INVALID_REPO_URL`. Same GitHub repo already tracked (matched by `github_id`, so old and new names of a renamed repo count as the same repo) → 409 `REPO_ALREADY_EXISTS`. Repo not found or token has no access → 422 `REPO_NOT_FOUND`. |
| US-2 | As an admin, I trigger a scan | 202 with a `queued` scan. The UI polls until `completed` or `failed`. If a scan is already queued or running → 409 `SCAN_IN_PROGRESS`. This is enforced by a DB unique index, not by a check-then-insert. |
| US-3 | As a user, I see a repo's score and per-rule results | The repo page shows score, grade and each rule's status, message and fix tip. Secret findings show only file and line, never the value. Partial and incomplete scans are labelled. |
| US-4 | As a user, I see score history | A line chart of completed scans' scores over time, plus a table of all past scans. |
| US-5 | As a user, I see all repos side by side | The dashboard table shows, per repo: latest completed score and grade, change since the previous completed scan, last scan time and status, and an Archived badge. It is sortable by score, name and last scanned. |
| US-6 | As a user, I see the most common failures | For each rule: how many repos fail or warn on it, based on each repo's latest completed scan. |
| US-7 | As an admin, I draft an issue | Creates one draft listing every `fail` and `warn` rule with its fix tip. Errors: no fail or warn → 422 `NOTHING_TO_REPORT`; repo already has an open draft → 409 `DRAFT_EXISTS`; repo archived or issues disabled → 422 `ISSUES_UNAVAILABLE`. |
| US-8 | As an admin, I edit and approve a draft | Title and body are editable while the draft is `drafted`. Approve → posted to GitHub **at most once**, even with double-clicks, two admins, timeouts or crashes. Then status is `posted` and the issue URL is saved. If posting fails, status is `approved` with `last_error`, and approve can be retried safely. |
| US-9 | Repos are re-scanned automatically | Every repo whose latest scan is older than `RESCAN_INTERVAL_HOURS`, or that has never been scanned, gets a `scheduled` scan. Exceptions: repos that are rate-limited, already scanning, or unreachable (last 3 scans failed with `REPO_NOT_FOUND`). |
| US-10 | As a viewer, I cannot change anything | Every mutating endpoint except `/auth/change-password` returns 403 `FORBIDDEN` for viewers. The UI hides admin-only controls. |

## 6. Architecture

```
 ┌───────────────┐   HTTPS/JSON   ┌──────────────────────────────────────────────┐
 │ React SPA     │ ─────────────▶ │ FastAPI app (ONE process, ONE worker)        │
 │ (Vite, TS)    │  Bearer JWT    │                                              │
 └───────────────┘                │  api/ ──▶ services/ ──▶ models (SQLAlchemy)  │
                                  │               │                              │
                                  │               ├──▶ github/ (httpx client)    │──▶ api.github.com
                                  │               │                              │──▶ codeload.github.com (tarballs)
                                  │               ├──▶ scanner/ (snapshot,       │
                                  │               │     rule engine, scoring)    │
                                  │               └──▶ scan worker (thread pool) │
                                  │  scheduler (APScheduler, in-process)         │
                                  └───────────────────┬──────────────────────────┘
                                                      ▼
                                          SQLite (dev/test) / PostgreSQL (prod)
```

Key decisions:
- **Exactly one backend process in v1.** That means 1 replica, `uvicorn --workers 1`, and the deploy strategy *Recreate* (no overlapping old and new containers). Startup recovery, the in-process executor, the scheduler, the login rate limiter and the rate-limit state all depend on this. Multi-replica support is a post-v1 item (§24).
- Scans run in an in-process `ThreadPoolExecutor` with `SCAN_WORKERS` threads (default 2). No Redis or Celery.
- **Sync SQLAlchemy 2.0.** FastAPI runs sync endpoints in its threadpool. Raise the AnyIO threadpool limit to 40 at startup.
- **Each repo is read with one tarball download**, processed in memory without extracting to disk. If the repo is too large, GitWell falls back to the trees API. See §8.

### 6.1 Database and session rules (MUST)
- **SQLite:**
  - `create_engine(url, connect_args={"check_same_thread": False})`.
  - Add a `connect` event listener that runs `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. Without `foreign_keys=ON`, cascades silently do nothing.
- **Postgres:** `pool_pre_ping=True`, `pool_size=20`, `max_overflow=20`.
- **API side:**
  - Request handlers use a per-request session (FastAPI dependency).
  - The API **commits** the `queued` scan row **before** calling `executor.submit(run_scan, scan_id)`.
  - Workers receive only the `scan_id`, never ORM objects or the request session.
- **Worker side:** the worker opens its own `with SessionLocal() as s:` and uses short transactions:
  1. Set `running` with a conditional update (`WHERE id=:id AND status='queued'`), then commit. If zero rows were updated, exit.
  2. Call GitHub and run the rules **with no open transaction**.
  3. Write the results, score and final status in one transaction, with a conditional update (`WHERE id=:id AND status='running'`). If zero rows were updated (for example, the repo was deleted in the meantime), discard the results and log `scan_orphaned` at INFO.
- **Timestamps:**
  - Every timestamp column uses a `UTCDateTime` TypeDecorator. On write it converts to UTC (and rejects naive datetimes); on read it attaches `tzinfo=UTC`.
  - Generate timestamps in Python with `datetime.now(UTC)`, never `server_default=func.now()`.
  - The API serializes timestamps in ISO 8601 with a `Z` suffix.
- **Alembic:**
  - `Base.metadata` uses this naming convention:
    ```python
    NAMING = {"ix": "ix_%(column_0_label)s", "uq": "uq_%(table_name)s_%(column_0_name)s",
              "ck": "ck_%(table_name)s_%(constraint_name)s",
              "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
              "pk": "pk_%(table_name)s"}
    ```
  - `env.py` sets `render_as_batch=True`, so SQLite can `ALTER`.
- **Enums:** store as strings, with `Enum(..., native_enum=False, length=20)`.
- **String limits:** Postgres enforces `VARCHAR(n)` and SQLite does not. The service layer therefore truncates `message` (1000), `error_message` and `last_error` (500) and `title` (256), ending with `…`.
- **Relationships:** use `passive_deletes=True` and let the DB cascade.

## 7. Tech stack (fixed)

| Area | Choice |
|---|---|
| Language | Python 3.12 |
| Web | FastAPI, Uvicorn |
| ORM and migrations | SQLAlchemy 2.0 (sync, typed `Mapped[]`), Alembic |
| Validation and settings | Pydantic v2, pydantic-settings, email-validator (for `EmailStr`) |
| HTTP client | httpx (sync) |
| Auth | PyJWT (HS256), pwdlib with Argon2 |
| Scheduler | APScheduler 3.x `BackgroundScheduler` |
| Metrics | prometheus-client |
| DB | SQLite (dev and tests), PostgreSQL 16 (prod, driver `psycopg[binary]` 3) |
| Frontend | React 18, Vite, TypeScript, React Router, TanStack Query, Tailwind CSS, Recharts, react-markdown (no raw HTML) |
| Tests | pytest, pytest-cov, respx, pytest-socket, freezegun; Vitest |
| Lint and types | ruff (lint and format), mypy (strict on `app/scanner`, `app/rules`, `app/github`); ESLint and tsc for the frontend |
| Packaging | uv (with `uv.lock` committed), npm (with `package-lock.json` committed), Docker, docker-compose |
| CI | GitHub Actions |

## 8. Scanning pipeline (core logic)

### 8.1 Flow
```
POST /repos/{id}/scan  (or on_add / scheduler)
  → INSERT scan(status=queued)  [partial unique index guarantees one active scan per repo]
  → COMMIT → executor.submit(run_scan, scan_id) → 202

run_scan(scan_id):
  deadline = now + SCAN_TIMEOUT_SECONDS
  1. tx: queued → running, started_at = now
  2. GET /repositories/{github_id}
       → refresh owner, name, full_name, url, default_branch, is_private, is_archived, has_issues
       → disabled = true → fail REPO_DISABLED
  3. GET /repos/{owner}/{name}/commits/{quote(default_branch, safe='')}
       with header  Accept: application/vnd.github.sha  (body = SHA)
       → 409 → fail REPO_EMPTY
  4. Build RepoSnapshot (§8.2), checking the deadline throughout
  5. Run every registered rule in display_order, isolating each with try/except and checking the deadline before each rule
  6. Compute score, grade, rules_errored, ruleset_version (§8.4)
  7. tx: write scan_results + scan fields, status=completed  (conditional on status='running')
  On exception: map to an error_code (§8.5), status=failed  (conditional on status='running')
```

**Cooperative timeout.** Python threads cannot be killed, so the deadline is checked in these places:
- after every chunk of the tarball download
- every 100 tar members
- every 100 files in the `secrets` rule
- before each rule

Every httpx call uses `timeout = min(configured_timeout, deadline - now)`. When the deadline passes, the code raises `ScanTimeout`, which becomes `SCAN_TIMEOUT`.

**Startup recovery:** any scan in `queued` or `running` becomes `failed` with `INTERRUPTED`. Any issue draft in `posting` becomes `approved` with `last_error = "Interrupted while posting; retry to continue."`

### 8.2 RepoSnapshot (the only thing rules can see)
Rules MUST NOT call GitHub, the DB or the filesystem. They receive a fully built snapshot; nothing is fetched lazily, so rule order cannot affect results.

```python
@dataclass(frozen=True)
class FileEntry:
    path: str      # POSIX, relative to repo root, no leading "/", e.g. "src/app/main.py"
    size: int      # bytes

class RepoSnapshot(Protocol):
    full_name: str
    default_branch: str
    commit_sha: str
    mode: Literal["archive", "api"]
    is_partial: bool                  # True if any file list or content is incomplete
    partial_reasons: list[str]        # e.g. ["tree_truncated", "text_budget_exceeded", "api_mode"]

    def files(self, include_ignored: bool = False) -> list[FileEntry]: ...   # sorted by path
    def dirs(self, include_ignored: bool = False) -> set[str]: ...           # all directory paths
    def root_file(self, names: Sequence[str]) -> FileEntry | None: ...
        # case-insensitive basename match among ROOT files only; if several match,
        # pick by order of `names`, then by path
    def glob(self, pattern: str, include_ignored: bool = False) -> list[FileEntry]: ...
        # case-insensitive on every OS; "*" and "?" do NOT cross "/"; "**/" matches zero or more dirs.
        # Implement via own regex translation (NOT fnmatch, which differs per OS).
    def read_text(self, path: str) -> str | None: ...
        # None if content not loaded, > MAX_FILE_BYTES, contains NUL in first 8 KB, or not UTF-8
        # decode with "utf-8-sig", errors="strict"
```

`IGNORED_DIRS`: directories with these names, at any depth, are left out of `files()` and `dirs()` unless `include_ignored=True`:
```
.git  node_modules  vendor  venv  .venv  dist  build  __pycache__  .tox  .mypy_cache  .pytest_cache  third_party  site-packages
```
Structural rules use the default (ignored dirs excluded). The `secrets` rule uses `include_ignored=True`, but always skips `.git/` and `node_modules/`.

**Choosing the snapshot mode.** `GET /repositories/{id}` returns `size` in KB.
- If `size > MAX_ARCHIVE_MB * 1024`, use **API mode**.
- Otherwise use **archive mode**. If the archive turns out to exceed a cap while streaming, abort it and fall back to API mode.
- Without `GITHUB_TOKEN`, API mode is not allowed (too many calls for the 60/h anonymous limit). The scan fails with `REPO_TOO_LARGE`.

**Archive mode (default). Everything stays in memory; nothing is extracted to disk.**
1. `GET /repos/{o}/{r}/tarball/{commit_sha}` with `follow_redirects=False`.
   - Expect a 302. Validate that `Location` uses scheme `https` and host **exactly** `codeload.github.com`; otherwise raise `GitHubError`.
   - GET the Location **without** the `Authorization` header (it holds its own short-lived token). Follow at most one redirect.
   - Never log this URL (§13 S6).
2. Stream into `tempfile.SpooledTemporaryFile(max_size=8 MB)`. Abort and fall back if the bytes exceed `MAX_ARCHIVE_MB`.
3. Iterate members with `tarfile.open(fileobj=..., mode="r:gz")`:
   - Accept only `member.isfile()`. Skip symlinks, hardlinks, devices and directories (count them in `details.skipped_links`). Directory paths are derived from file paths.
   - Normalise the name with `PurePosixPath`, **drop the first path component** (whatever it is called), and reject names that are absolute or contain `..`.
   - Keep the file's bytes in memory only if `size <= MAX_FILE_BYTES` **and** the running total of kept bytes is ≤ `MAX_TEXT_TOTAL_MB`. Otherwise record only its `FileEntry`. If the total budget is exceeded, add `text_budget_exceeded`.
   - Stop reading members (set `is_partial`, reason `archive_limits`) if the member count exceeds `MAX_FILES` or the sum of `member.size` exceeds `MAX_UNCOMPRESSED_MB`. This prevents decompression bombs.
4. Never execute anything from the archive.

**API mode (fallback for large repos):**
1. `GET /repos/{o}/{r}/git/trees/{commit_sha}?recursive=1` provides the file list. If the response has `truncated: true`, add the reason `tree_truncated`.
2. **Eagerly prefetch**, before any rule runs, the content of this fixed set, in this priority order, up to `API_SNAPSHOT_MAX_FETCHES` files:
   - root README, LICENSE, COPYING and UNLICENSE variants
   - root `.gitignore`
   - dependency manifests and lock files at depth ≤ 2 (lock files are only checked for existence, not fetched)
   - root, `.github/` and `docs/` CONTRIBUTING files
   - `.github/workflows/*`

   Fetch each one with `GET /repos/{o}/{r}/contents/{path}?ref={sha}` and header `Accept: application/vnd.github.raw+json`. Skip files whose tree `size > MAX_FILE_BYTES` without fetching them.
3. `read_text` returns content only for prefetched files. `mode = "api"`, `is_partial = True`, and the reasons include `api_mode`.

### 8.3 Rule plugin contract

Directory: `backend/app/rules/`. **One file per rule.** Rules are auto-discovered at startup with `pkgutil.iter_modules`: any module with a top-level `rule` object that is an instance of `Rule` is registered. A duplicate `id` or duplicate `display_order` is a startup error.

```python
class Status(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"   # rule doesn't apply (e.g. no dependency manifest)
    ERROR = "error"                     # rule crashed; recorded, scan continues

@dataclass
class RuleResult:
    status: Status
    message: str                        # 1–2 sentences, specific to this repo
    details: dict[str, Any] = field(default_factory=dict)   # JSON-serializable, NEVER secret values

class Rule(ABC):
    id: ClassVar[str]             # stable snake_case; never renamed
    name: ClassVar[str]           # display name
    description: ClassVar[str]
    weight: ClassVar[int]
    display_order: ClassVar[int]  # UI, API and issue body order
    fix_tip: ClassVar[str]        # markdown

    @abstractmethod
    def check(self, snap: RepoSnapshot) -> RuleResult: ...
```

- The engine runs rules sorted by `display_order`.
- If `check` raises, the engine records `ERROR` with the message `"Rule failed to run."`. The exception *type* is logged with its stack trace; the message is not stored, because it could contain repo content.
- Adding a rule means adding `backend/app/rules/<id>.py` and `backend/tests/rules/test_<id>.py`. The UI renders rules in API order, so it needs no change.

### 8.4 Scoring (deterministic)

- Values: `pass = 1.0`, `warn = 0.5`, `fail = 0.0`. `not_applicable` and `error` are excluded from both the numerator and the denominator.
- `score = round(100 * Σ(weight × value) / Σ(weight))`, using Python's `round`.
- `score = null` (and `grade = null`) if no rule counted, **or if the `secrets` rule errored**. A crash in the highest-weight safety rule must not inflate the score.
- `scans.rules_errored` = the number of `error` results. When it is > 0, the UI shows "Incomplete score (N rules errored)".
- `scans.ruleset_version` = the first 12 hex characters of `sha256(",".join(sorted(f"{id}:{weight}" for each rule)))`.
- `score_delta` (repo summary) = latest completed score minus the previous completed score. It is `null` if either score is null or the two scans' `ruleset_version` differ.
- Grades: `A` ≥ 90, `B` ≥ 75, `C` ≥ 60, `D` ≥ 40, `F` < 40.

### 8.5 Scan error codes
`error_message` always comes from this fixed catalog. It is **never** built from `str(exception)`.

| `error_code` | `error_message` |
|---|---|
| `REPO_NOT_FOUND` | Repository not found, or the token cannot access it. |
| `REPO_EMPTY` | Repository has no commits. |
| `REPO_DISABLED` | Repository is disabled on GitHub. |
| `REPO_TOO_LARGE` | Repository is too large to scan without a GitHub token. |
| `GITHUB_RATE_LIMITED` | GitHub rate limit reached; try again after the reset time. |
| `GITHUB_UNAVAILABLE` | GitHub is unavailable or timed out. |
| `GITHUB_ERROR` | Unexpected response from GitHub. |
| `SCAN_TIMEOUT` | Scan exceeded the time limit. |
| `INTERRUPTED` | Scan was interrupted by a server restart. |
| `INTERNAL_ERROR` | Unexpected internal error. |

## 9. Rule specifications (v1)

"Root" means the repo's top-level directory. Name matching is **case-insensitive** unless stated otherwise. Rules use `root_file`, `glob`, `files` and `read_text` from §8.2.

**Emptiness rule (all rules):** a file is *empty* if `size == 0`, or if `read_text` returns a string that is empty after `strip()`. If `read_text` returns `None` and `size > 0` (for example, too large or not UTF-8), the file counts as **non-empty**.

### 9.1 `readme`: README (weight 15, display_order 20)
- File: `root_file(["README.md", "README", "README.rst", "README.txt", "README.markdown"])`.
- **fail:** not found, or empty.
- **warn:** content is loaded and has fewer than 150 non-whitespace characters. Message: "README is very short (N characters)."
- **pass:** otherwise.
- `details`: `{"path": "README.md", "length": 1234}`. `length` is the non-whitespace character count, or `size` if the content was not loaded.
- Fix tip: add a README covering what the project does, how to install, run and test it, and how to contribute.

### 9.2 `license`: License (weight 10, display_order 50)
- File: a root file whose name **without extension** is `LICENSE`, `LICENCE`, `COPYING` or `UNLICENSE`.
- **fail:** none. **warn:** found but empty. **pass:** otherwise.
- Fix tip: add a LICENSE file (see choosealicense.com). On GitHub: Add file → Create new file → name it `LICENSE` → "Choose a license template".

### 9.3 `tests`: Tests (weight 15, display_order 30)
Test file patterns (matched on the **basename**). Brace expansion is written out here, so implement this exact list:
```
test_*.py  *_test.py
*.test.js  *.test.jsx  *.test.ts  *.test.tsx  *.test.mjs  *.test.cjs
*.spec.js  *.spec.jsx  *.spec.ts  *.spec.tsx
*_test.go  *_spec.rb
```
Case-sensitive patterns (match the basename exactly): `*Test.java`, `*Tests.java`, `*Test.kt`, `*Tests.cs`.

Test directory names (any depth): `test`, `tests`, `__tests__`, `spec`, `specs`.

Source extensions: `.py .js .jsx .ts .tsx .mjs .cjs .go .java .kt .cs .rb .rs .php .swift`.

- **pass:** at least one file matches a test-file pattern, **or** at least one source file sits under a test directory (this covers unittest-style `tests/api.py`).
- **warn:** a test directory exists but contains only non-source files (fixtures, data, `.gitkeep`).
- **fail:** otherwise.
- `details`: `{"test_file_count": 42, "sample": ["tests/test_api.py"]}`. The sample has at most 5 paths, sorted.
- Fix tip: add a test framework (pytest, Vitest/Jest, go test, …) and at least one test, and run it in CI.

### 9.4 `ci`: Continuous integration (weight 15, display_order 40)
- GitHub Actions: files matching `.github/workflows/*.yml` or `.github/workflows/*.yaml`, as **direct children only** (GitHub does not run workflows in subfolders).
- Other providers, at root: `.gitlab-ci.yml`, `.circleci/config.yml`, `Jenkinsfile`, `azure-pipelines.yml`, `.travis.yml`, `bitbucket-pipelines.yml`.
- **pass:** at least one non-empty CI file. **warn:** CI files exist but all are empty. **fail:** none.
- `details`: `{"providers": ["github_actions"], "files": [".github/workflows/ci.yml"]}`
- Fix tip: add `.github/workflows/ci.yml` that installs dependencies, lints and runs the tests on every push and pull request. The tip includes a minimal 15-line example.

### 9.5 `secrets`: No committed secrets (weight 20, display_order 10)

**Scope:**
- Every file in `files(include_ignored=True)`, except paths under `.git/` or `node_modules/`.
- Skip files whose basename is a lock file (`*.lock`, `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml`, `yarn.lock`, `go.sum`), or that end in `.min.js` or `.map`.
- Content is scanned only where `read_text` is non-None. Lines longer than 10,000 characters are skipped and counted in `lines_skipped_long`.
- Files are processed in sorted path order.

**Patterns** (compile exactly as written):
```python
HIGH_CONFIDENCE = {
    "aws_access_key_id":       r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])",
    "github_token":            r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,255}(?![A-Za-z0-9_])",
    "github_fine_grained_pat": r"(?<![A-Za-z0-9_])github_pat_[A-Za-z0-9_]{60,255}(?![A-Za-z0-9_])",
    "slack_token":             r"(?<![A-Za-z0-9-])xox[abprs]-[A-Za-z0-9-]{10,250}(?![A-Za-z0-9-])",
    "google_api_key":          r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])",
    "stripe_live_key":         r"(?<![A-Za-z0-9_])(?:sk|rk)_live_[0-9A-Za-z]{24,247}(?![0-9A-Za-z])",
    "private_key":             r"-----BEGIN (?:(?:RSA|EC|DSA|OPENSSH|PGP|ENCRYPTED) )?PRIVATE KEY(?: BLOCK)?-----",
}
LOW_CONFIDENCE = {
    # key may be quoted (JSON/YAML); value must be quoted. Unquoted .env-style values are intentionally not matched.
    "generic_secret_assignment":
        r"""(?i)["']?\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b["']?\s*[:=]\s*["']([^"'\s]{8,})["']""",
}
PLACEHOLDER_MARKERS = ["example", "changeme", "change_me", "placeholder", "your_", "your-", "xxxx",
                       "dummy", "test", "sample", "fake", "<", "${", "{{", "os.environ", "process.env"]
TEST_PATH_SEGMENTS = {"test", "tests", "__tests__", "spec", "specs", "fixtures", "testdata",
                      "examples", "example", "docs"}
ENV_FILE_ALLOWED = {".env.example", ".env.sample", ".env.template", ".env.dist"}
```

**Confidence assignment:**
1. A high-confidence match is **ignored** if the matched text contains `EXAMPLE` (case-insensitive). This covers AWS's documented sample key.
2. A high-confidence match is downgraded to **medium** if any directory segment of its path is in `TEST_PATH_SEGMENTS` (case-insensitive).
3. **`committed_env_file`** (file-level, `line = null`, confidence **medium**) applies to any file at any depth whose lowercased basename is `.env`, or starts with `.env.` and is not in `ENV_FILE_ALLOWED`. Framework `.env.development` files are often public config, so this is medium; any real key inside such a file still produces its own high finding.
4. A generic match is **ignored** if the captured value, lowercased, contains any `PLACEHOLDER_MARKERS` entry. Otherwise it is **low** confidence.

**Result:**
- **fail:** any high finding.
- **warn:** any medium or low finding, **or** no findings but `snapshot.is_partial`. In API mode only `committed_env_file` is checked, and the message is "Secret scan limited: repository too large for a full content scan."
- **pass:** no findings. Message: "No secrets found in the current tree of {branch}@{short_sha}. Git history is not scanned."
- Failure and warning message: `"{n} potential secret(s) found in {f} file(s)."`

**Privacy (non-negotiable):** the matched value MUST NOT be stored, logged, returned by the API or included in an issue. A finding records only its location, sorted by path then line, with one finding per (path, line, pattern), capped at 100 (`truncated: true` when exceeded):
```json
{"findings": [{"path": "config/settings.py", "line": 12, "pattern": "aws_access_key_id", "confidence": "high"}],
 "files_scanned": 812, "files_skipped_size": 3, "files_skipped_binary": 40, "lines_skipped_long": 0,
 "truncated": false, "partial": false}
```
`line` is 1-based. `files_scanned` counts files whose `read_text` was non-None.

Fix tip: **revoke and rotate the credential first.** Then remove it from the code, load it from environment variables or a secret manager, and purge it from git history (`git filter-repo` or BFG). Deleting the file is not enough, because the secret stays in history.

### 9.6 `dependencies`: Pinned dependencies (weight 10, display_order 60)

**Manifests:** search at depth ≤ 2 (root, `*/` and `*/*/`), excluding `IGNORED_DIRS`. A lock file only counts if it is in **the same directory** as its manifest.

Each manifest gets its own verdict. The rule result is the **worst** verdict (`fail` > `warn` > `pass`).

| Manifest | Lock files | Verdict |
|---|---|---|
| `requirements*.txt` | none | Count requirement lines (see below). 0 unpinned → pass; ≤ 50% unpinned → warn; more than 50% → fail |
| `pyproject.toml` | `poetry.lock`, `uv.lock`, `pdm.lock` | Lock present → pass. Otherwise all deps exact → pass, else warn ("No lock file: fine for a library, required for an application") |
| `package.json` | `package-lock.json`, `npm-shrinkwrap.json`, `yarn.lock`, `pnpm-lock.yaml`, `bun.lockb`, `bun.lock` | Lock present → pass. Otherwise all deps exact → pass, else warn (same message) |
| `Cargo.toml` | `Cargo.lock` | Lock present → pass, else warn |
| `go.mod` | `go.sum` | No `require` directive → manifest ignored. Otherwise lock present → pass, else **fail** |
| `Gemfile` | `Gemfile.lock` | lock → pass, else **fail** |
| `Pipfile` | `Pipfile.lock` | lock → pass, else **fail** |

**Parsing details:**
- **`requirements*.txt`:**
  - Strip inline `# comments` and environment markers (`;` and everything after it).
  - Join lines that end in `\`.
  - Ignore blank lines and lines starting with `-r`, `-c`, `-e`, `-i`, `-f`, `--` (this covers `--index-url`, `--extra-index-url`, `--find-links`, `--trusted-host` and `--hash`).
  - A requirement is **pinned** if it contains `==` or `===`, or if it is a URL or VCS reference containing `@` followed by a 40-character hex SHA.
- **`pyproject.toml`:**
  - Read with `tomllib`.
  - Use `[project].dependencies`, or else `[tool.poetry.dependencies]` without the `python` key.
  - Pinned means a PEP 508 `==` constraint, or a Poetry version string that is an exact version (no `^ ~ * > < ,`).
  - If there are no dependencies at all, ignore the manifest.
- **`package.json`:**
  - Look at `dependencies` and `devDependencies`.
  - "Exact" means the version matches `^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$`.
  - `workspace:`, `file:`, `link:`, `git+…` and URLs count as unpinned.
- **Unparseable manifest:** verdict warn, with `details` noting `"parse_error": true`.

**Overall result:**
- **not_applicable:** no counted manifests.
- Otherwise the worst manifest verdict.

`details`:
```json
{"manifests": [{"path": "backend/pyproject.toml", "ecosystem": "python", "lock_file": "backend/uv.lock",
                "verdict": "pass", "total": 14, "unpinned": []}]}
```
`unpinned` lists **package names only**, normalised (PEP 503 for Python, the key for npm), at most 20. Never store version specs, URLs or raw lines, because they can contain credentials.

Fix tip: commit a lock file (`uv lock`, `poetry lock`, `npm install` → `package-lock.json`), or pin exact versions (`pip-compile`, `pip freeze`).

### 9.7 `gitignore`: .gitignore (weight 10, display_order 70)
- **fail:** no root `.gitignore`.
- A line **excludes `.env`** if, after trimming, it is not a comment or negation and it is one of these:
  ```
  .env   .env*   .env.*   *.env   /.env   **/.env   /.env*   **/.env*
  ```
- **"The repo uses dotenv"** means any of:
  - any file at any depth whose basename starts with `.env`
  - any dependency manifest (§9.6) whose loaded text contains `dotenv`
- **warn:** `.gitignore` is present, the repo uses dotenv, and `.env` is not excluded. Message: "`.gitignore` does not exclude `.env` files."
- **pass:** otherwise.
- `details`: `{"excludes_env": true, "uses_dotenv": true}`
- Fix tip: start from a github.com/github/gitignore template and add `.env` and `.env.*`, plus `!.env.example`.

### 9.8 `docs`: Contributor docs (weight 5, display_order 80)
- **pass:** a file named `CONTRIBUTING`, `CONTRIBUTING.md` or `CONTRIBUTING.rst` exists at the root, in `.github/` or in `docs/`, **or** a root `docs/` directory contains at least one file.
- **fail:** otherwise.
- Fix tip: add `CONTRIBUTING.md` covering dev setup, branching, the PR process and code style, and/or a `docs/` folder.

### 9.9 Weights and order
| display_order | Rule | Weight |
|---|---|---|
| 10 | `secrets` | 20 |
| 20 | `readme` | 15 |
| 30 | `tests` | 15 |
| 40 | `ci` | 15 |
| 50 | `license` | 10 |
| 60 | `dependencies` | 10 |
| 70 | `gitignore` | 10 |
| 80 | `docs` | 5 |
| | **Total** | **100** |

## 10. Data model

Every table has an integer `id` primary key. All timestamps are `UTCDateTime` (§6.1). Enum values are listed in code blocks below the tables.

### `users`
| Column | Type | Notes |
|---|---|---|
| `email` | str(320) | unique, stored lowercased; validated as `EmailStr` |
| `password_hash` | str(255) | Argon2 |
| `role` | enum `UserRole` | default `viewer` |
| `is_active` | bool | default true |
| `token_version` | int | default 0; incremented on role, active or password change, which invalidates old JWTs |
| `created_at`, `updated_at` | datetime | |

### `repos`
| Column | Type | Notes |
|---|---|---|
| `github_id` | bigint | **unique**, not null; GitHub's numeric repo id (survives renames) |
| `url` | str(300) | canonical `https://github.com/{owner}/{name}`, refreshed each scan |
| `owner` | str(39) | refreshed each scan |
| `name` | str(100) | refreshed each scan |
| `full_name` | str(140) | `owner/name` lowercased, unique, refreshed each scan |
| `default_branch` | str(255) | refreshed each scan |
| `is_private` | bool | refreshed each scan |
| `is_archived` | bool | refreshed each scan |
| `has_issues` | bool | refreshed each scan |
| `added_by_id` | FK users.id, nullable | `ON DELETE SET NULL` |
| `added_at` | datetime | |

### `scans`
| Column | Type | Notes |
|---|---|---|
| `repo_id` | FK repos.id | `ON DELETE CASCADE` |
| `status` | enum `ScanStatus` | |
| `trigger` | enum `ScanTrigger` | |
| `triggered_by_id` | FK users.id, nullable | `ON DELETE SET NULL`; null when scheduled |
| `score` | int, nullable | |
| `grade` | str(1), nullable | |
| `rules_errored` | int | default 0 |
| `ruleset_version` | str(12), nullable | |
| `commit_sha` | str(64), nullable | 64 characters allows SHA-256 repositories |
| `snapshot_mode` | str(10), nullable | `archive` or `api` |
| `is_partial` | bool | default false |
| `error_code` | str(50), nullable | from §8.5 |
| `error_message` | str(500), nullable | from §8.5 catalog only |
| `created_at`, `started_at`, `finished_at` | datetime | started and finished are nullable |

Indexes:
```
ux_scans_active   UNIQUE (repo_id) WHERE status IN ('queued','running')   -- sqlite_where + postgresql_where
ix_scans_repo_status_created  (repo_id, status, created_at)
ix_scans_status   (status)
```

### `scan_results`
| Column | Type | Notes |
|---|---|---|
| `scan_id` | FK scans.id | `ON DELETE CASCADE` |
| `rule_id` | str(50) | |
| `status` | enum `RuleStatus` | |
| `message` | str(1000) | |
| `details` | JSON | portable `sa.JSON`; never contains secret values |
| `weight` | int | snapshot of the rule's weight at scan time |
| `display_order` | int | snapshot of the rule's order at scan time |

Unique constraint: `(scan_id, rule_id)`.

### `issue_drafts`
| Column | Type | Notes |
|---|---|---|
| `scan_id` | FK scans.id | `ON DELETE CASCADE` |
| `repo_id` | FK repos.id | `ON DELETE CASCADE` |
| `title` | str(256) | 1–256 chars |
| `body` | text | 1–60,000 chars |
| `status` | enum `DraftStatus` | |
| `created_by_id`, `approved_by_id` | FK users.id, nullable | `ON DELETE SET NULL` |
| `approved_at` | datetime, nullable | |
| `post_attempts` | int | default 0; incremented right **before** each POST |
| `github_issue_number` | int, nullable | |
| `github_issue_url` | str(300), nullable | |
| `last_error` | str(500), nullable | |
| `created_at`, `updated_at` | datetime | |

Indexes: `(repo_id, status)` and `(scan_id)`.

Enum values:
```
UserRole:    viewer, admin
ScanStatus:  queued, running, completed, failed
ScanTrigger: manual, scheduled, on_add
RuleStatus:  pass, warn, fail, not_applicable, error
DraftStatus: drafted, approved, posting, posted, discarded
```

### 10.1 Issue draft state machine
| From | Action | To |
|---|---|---|
| `drafted` | edit (PATCH) | `drafted` |
| `drafted` | approve | `posting`, then `posted` or `approved` |
| `approved` | approve (retry) | `posting`, then `posted` or `approved` |
| `drafted` | discard | `discarded` |
| `approved` | discard | `discarded` |
| `posting` | startup recovery | `approved` |

Any other transition returns 409 `INVALID_STATE`. A repo can have **at most one open draft** at a time; open means `drafted`, `approved` or `posting`.

## 11. API specification

- Base path `/api/v1`. The exceptions are `/health` and `/metrics`, which are at the root.
- JSON only. Auth: `Authorization: Bearer <JWT>`.
- OpenAPI docs at `/docs` only when `ENV=development`.
- **Lists:** `/rules` returns a bare array. Every other list returns `{"items": [...], "total": N}` and accepts `limit` (default 50, max 200) and `offset`.

### 11.1 Error format
Every non-2xx response from `/api/v1` has this shape:
```json
{"error": {"code": "REPO_ALREADY_EXISTS", "message": "This repository is already being tracked.", "details": {}}}
```
FastAPI's default 422 is overridden to use this shape, with `details.fields`. `/health` and `/metrics` are exempt from this format.

| HTTP | Codes |
|---|---|
| 400 | `BAD_REQUEST` |
| 401 | `UNAUTHENTICATED`, `INVALID_CREDENTIALS` |
| 403 | `FORBIDDEN`, `REGISTRATION_DISABLED` |
| 404 | `NOT_FOUND` |
| 409 | `REPO_ALREADY_EXISTS`, `SCAN_IN_PROGRESS`, `INVALID_STATE`, `EMAIL_TAKEN`, `DRAFT_EXISTS`, `LAST_ADMIN` |
| 422 | `VALIDATION_ERROR`, `INVALID_REPO_URL`, `REPO_NOT_FOUND`, `NOTHING_TO_REPORT`, `GITHUB_TOKEN_REQUIRED`, `ISSUES_UNAVAILABLE`, `NO_REPO_PERMISSION` |
| 429 | `TOO_MANY_ATTEMPTS` (with a `Retry-After` header) |
| 500 | `INTERNAL_ERROR` (`details.request_id`) |
| 502 | `GITHUB_ERROR` |
| 503 | `GITHUB_RATE_LIMITED` (`details.reset_at`), `GITHUB_UNAVAILABLE` |

### 11.2 Endpoints

| Method | Path | Role | Request | Success |
|---|---|---|---|---|
| GET | `/config` | public | none | 200 `{allow_registration, github_token_configured, version}` |
| POST | `/auth/register` | public | `{email, password}` | 201 `User` (403 if disabled) |
| POST | `/auth/login` | public | `{email, password}` | 200 `{access_token, token_type: "bearer", expires_in, user}` |
| GET | `/auth/me` | any | none | 200 `User` |
| POST | `/auth/change-password` | any | `{current_password, new_password}` | 204 (old tokens stop working) |
| GET | `/users` | admin | list | 200 list of `User` |
| POST | `/users` | admin | `{email, password, role}` | 201 `User` |
| PATCH | `/users/{id}` | admin | `{role?, is_active?}` | 200 `User` |
| GET | `/rules` | any | none | 200 `[{id, name, description, weight, display_order, fix_tip}]` sorted by `display_order` |
| POST | `/repos` | admin | `{url}` | 201 `RepoSummary` (an `on_add` scan is queued) |
| GET | `/repos` | any | `sort` = `name`, `score` or `last_scanned` (default `name`); `order` = `asc` or `desc` (default `asc`) | 200 list of `RepoSummary` |
| GET | `/repos/{id}` | any | none | 200 `RepoSummary` |
| DELETE | `/repos/{id}` | admin | none | 204; cascades to scans and drafts; allowed while a scan is active |
| POST | `/repos/{id}/scan` | admin | none | 202 `Scan` |
| GET | `/repos/{id}/scans` | any | list, `?status=`, newest first | 200 list of `Scan` |
| GET | `/scans/{id}` | any | none | 200 `ScanDetail` |
| POST | `/scans/{id}/issue-draft` | admin | none | 201 `IssueDraft` |
| GET | `/issue-drafts` | any | list, `?repo_id=&status=` | 200 list of `IssueDraft` |
| GET | `/issue-drafts/{id}` | any | none | 200 `IssueDraft` |
| PATCH | `/issue-drafts/{id}` | admin | `{title?, body?}` | 200 (only while `drafted`) |
| POST | `/issue-drafts/{id}/approve` | admin | none | 200 `IssueDraft` with status `posted` (§12.3) |
| POST | `/issue-drafts/{id}/discard` | admin | none | 200 `IssueDraft` |
| GET | `/dashboard/summary` | any | none | 200 `DashboardSummary` |
| GET | `/health` | public | none | 200 or 503 |
| GET | `/metrics` | token | none | Prometheus text (§14) |

Endpoint rules:
- **`GET /repos` sorting:** `score` sorts by `latest_completed_scan.score`, and `last_scanned` by `latest_scan.created_at`. Nulls always sort last, in either direction. Ties break by `full_name`.
- **`POST /repos` flow:**
  1. Validate the URL (§12.1).
  2. `GET /repos/{owner}/{name}`. This may 301 to the same API host after a rename; follow it.
  3. If the returned `id` is already tracked → 409.
  4. Insert the repo with GitHub's casing of the names.
  5. Commit, then queue the `on_add` scan.
- **`POST /scans/{id}/issue-draft`:** the scan must be `completed` (otherwise 409 `INVALID_STATE`). Then check in this order: open draft exists → 409 `DRAFT_EXISTS` (`details.draft_id`); `is_archived` or `!has_issues` → 422 `ISSUES_UNAVAILABLE`; no fail or warn results → 422 `NOTHING_TO_REPORT`.
- **Password rules:** 8–128 characters; otherwise 422 `VALIDATION_ERROR`. Emails are lowercased on register, login and create.

### 11.3 Schemas

```jsonc
// User
{"id": 1, "email": "a@b.com", "role": "admin", "is_active": true, "created_at": "2026-11-18T10:00:00Z"}

// RepoSummary
{"id": 3, "github_id": 1362490, "url": "https://github.com/psf/requests", "full_name": "psf/requests",
 "default_branch": "main", "is_private": false, "is_archived": false, "has_issues": true, "added_at": "...",
 "latest_scan": {"id": 10, "status": "failed", "error_code": "GITHUB_RATE_LIMITED", "created_at": "...", "finished_at": "..."},  // newest, any status, or null
 "latest_completed_scan": {"id": 9, "score": 82, "grade": "B", "commit_sha": "abc…", "finished_at": "...",
                           "rules_errored": 0, "is_partial": false, "has_findings": true},                               // or null
 "score_delta": 7,              // or null (§8.4)
 "active_scan_id": null,        // id of queued/running scan, or null
 "open_draft_id": null}         // id of drafted/approved/posting draft, or null

// Scan
{"id": 9, "repo_id": 3, "status": "completed", "trigger": "manual", "score": 82, "grade": "B",
 "rules_errored": 0, "ruleset_version": "a1b2c3d4e5f6", "commit_sha": "abc…", "snapshot_mode": "archive",
 "is_partial": false, "error_code": null, "error_message": null,
 "created_at": "...", "started_at": "...", "finished_at": "..."}

// ScanDetail = Scan + results (sorted by display_order)
{"...": "...", "results": [
  {"rule_id": "secrets", "rule_name": "No committed secrets", "status": "fail", "weight": 20, "display_order": 10,
   "message": "2 potential secret(s) found in 1 file(s).",
   "details": {"findings": [{"path": "app/config.py", "line": 12, "pattern": "aws_access_key_id", "confidence": "high"}]},
   "fix_tip": "Revoke and rotate the credential first…"}]}
   // rule_name and fix_tip come from the current registry; if a rule no longer exists, use rule_id and an empty fix_tip

// IssueDraft
{"id": 4, "scan_id": 9, "repo_id": 3, "repo_full_name": "psf/requests", "repo_is_private": false,
 "title": "...", "body": "...", "status": "posted", "post_attempts": 1,
 "github_issue_number": 123, "github_issue_url": "https://github.com/...", "last_error": null,
 "created_by_id": 1, "approved_by_id": 1, "created_at": "...", "updated_at": "...", "approved_at": "..."}

// DashboardSummary
{"repo_count": 12, "scanned_repo_count": 11, "average_score": 71.4,
 "grade_distribution": {"A": 2, "B": 4, "C": 3, "D": 1, "F": 1},
 "rule_failures": [{"rule_id": "tests", "rule_name": "Tests", "fail": 5, "warn": 1}],
 "score_trend": [{"date": "2026-10-20", "average_score": 65.0}]}
```

**DashboardSummary rules:**
- **Base data:** each repo's latest completed scan.
- `scanned_repo_count` counts repos that have one. Scans with `score = null` count here but are excluded from the average and from the grade distribution.
- `average_score` is rounded to 1 decimal, or null when no repo has a score.
- `grade_distribution` always contains all five keys.
- `rule_failures` includes **every registered rule**, including 0/0 rows. It is sorted by fail descending, then warn descending, then `display_order`.
- `score_trend` covers the 30 UTC days D-29…D, computed in Python:
  1. Load each repo's latest completed scan **before** the window (the baseline), plus every completed scan inside the window.
  2. Walk forward day by day, carrying each repo's most recent score (by `finished_at`).
  3. A day's value is the average over repos that have a score. Days with no data are omitted.

### 11.4 Health
`GET /health` returns `{"status": "ok", "version": "1.0.0", "db": "ok"}`. If `SELECT 1` fails, it returns 503 with `"db": "error"`. It never calls GitHub.

## 12. GitHub integration

- Base URL: `GITHUB_API_URL` (default `https://api.github.com`; tests override it). Every request sends these headers:
  ```
  Accept: application/vnd.github+json
  X-GitHub-Api-Version: 2022-11-28
  User-Agent: GitWell/1.0
  Authorization: Bearer $GITHUB_TOKEN      (only if set; never sent to codeload)
  ```
  The header is overridden per call where §8 says so (`vnd.github.sha`, `vnd.github.raw+json`).
- **Redirects:** API calls follow redirects only to the same API host (at most 3), which handles renamed repos. Tarball redirects are handled as in §8.2. Any other host is an error.
- **Tokens:**
  - `GITHUB_TOKEN` is used for reading. It needs Contents: read and Metadata: read (fine-grained), or `repo` / `public_repo` (classic).
  - `GITHUB_ISSUES_TOKEN` (optional) is used only for posting issues. It needs Issues: read & write. If it is not set, `GITHUB_TOKEN` is used.
  - A fine-grained PAT covers a single resource owner. Document this in the README; a GitHub App is the post-v1 path.
- **Without any token:** public repos still scan (archive mode only), but GitHub allows only 60 requests/hour. Log a startup warning.
- **Timeouts:** connect 5 s and read 20 s (tarball read 60 s), always clamped to the scan deadline.
- **Retries:** up to 2 retries, with backoff of 1 s then 2 s, on network errors and on 502, 503 and 504. **Never** retry other 4xx responses. **Never** auto-retry `POST /issues`.
- **Classifying errors.** Every response carries `X-RateLimit-*` headers, so classify in this order:
  1. 403 or 429 with a `Retry-After` header → `GitHubRateLimited`, `reset_at = now + Retry-After`.
  2. 403 or 429 with `X-RateLimit-Remaining: 0` → `GitHubRateLimited`, `reset_at = X-RateLimit-Reset`.
  3. 403 or 429 whose body `message` contains `secondary rate limit` → `GitHubRateLimited`, `reset_at = now + 60 s`.
  4. 403 with an `X-GitHub-SSO` header → `GitHubNotFound`, `details.reason = "sso_required"`.
  5. 404, 451, and any other 403 on a repo lookup → `GitHubNotFound`.
  6. 409 on the commits call → `GitHubEmptyRepo`.
  7. 5xx or timeout after retries → `GitHubUnavailable`. Anything else → `GitHubError`.
- **Process-wide `RateLimitState`:** after every response, record `remaining` and `reset_at`. This is safe because there is exactly one process. The scheduler reads it (§14), and the `gitwell_github_rate_limit_remaining` gauge reports it.
- **Exceptions:** `app/github/errors.py` defines the typed exceptions. A single exception handler maps them to API errors and scan `error_code` values.

### 12.1 URL validation (strict)
After `strip()`, the URL must match:
```
^https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/(?P<name>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$
```
Then also reject it if `"--" in owner` or `name in {".", "..", ".git"}`.

Everything else is rejected: `http://`, `www.`, uppercase scheme or host, other hosts, extra path segments (`/tree/main`), query strings, fragments, and credentials in the URL.

The test table has ≥ 20 cases, including every rejection above, the `.git` suffix, a trailing slash and dots in names.

### 12.2 Issue draft content
**Title:** `GitWell: {n} quality check(s) need attention (score {score}/100)`, where `n` is the number of fail plus warn results. If the score is null, the title ends "(score unavailable)".

**Body (markdown):**
```markdown
GitWell scanned this repository at commit `{short_sha}` on {YYYY-MM-DD} and found the following:

## ❌ Failing
- [ ] **Tests**: No test files or test directories found.
  <fix tip, every line indented by 2 spaces>

## ⚠️ Warnings
- [ ] **README**: README is very short (84 characters).
  <fix tip, indented>

---
_Generated by GitWell. Score: {score}/100 ({grade})._
```
- Items follow `display_order`. Omit a section if it is empty. `short_sha` is the first 7 characters. The date is the scan's `finished_at` in UTC.
- **Secrets special case:** never include paths, line numbers or pattern ids, because the issue may be public. The item reads: `**No committed secrets**: Potential secrets were detected. A maintainer should review the findings in GitWell privately, rotate the affected credentials and purge them from git history.` The fix tip still follows.
- **Injection safety:** any string taken from the repo (file paths, package names, messages that include them) goes inside inline code with backticks removed, and each `@` becomes `@` + U+200B, so no one gets pinged by an @-mention.
- If the generated body is longer than 60,000 characters, truncate it and append `_(truncated)_`.

### 12.3 Approve and post (exactly once)
`POST /issue-drafts/{id}/approve`:
1. If neither `GITHUB_ISSUES_TOKEN` nor `GITHUB_TOKEN` is set → 422 `GITHUB_TOKEN_REQUIRED`. No state changes.
2. Run an atomic claim and commit:
   ```sql
   UPDATE issue_drafts SET status='posting', approved_by_id=:me, approved_at=COALESCE(approved_at,:now), updated_at=:now
   WHERE id=:id AND status IN ('drafted','approved')
   ```
   If zero rows were updated → 409 `INVALID_STATE`. This handles double-clicks and two admins approving at once.
3. `GET /repositories/{github_id}`, then check:
   - archived or `has_issues=false` → `ISSUES_UNAVAILABLE`
   - `permissions.push` and `permissions.triage` both false → `NO_REPO_PERMISSION`

   In both cases, set status back to `approved` with `last_error`, and return 422.
4. **If `post_attempts > 0`** (an earlier attempt may have succeeded): `GET /repos/{o}/{r}/issues?state=all&since={approved_at}&per_page=100`. If any issue's body contains the marker `<!-- gitwell-draft:{id} -->`, adopt it: set status `posted` with its number and URL, and return 200.
5. Increment `post_attempts` and commit. Then `POST /repos/{o}/{r}/issues` with the title, and the body plus `\n\n<!-- gitwell-draft:{id} -->`. The marker is added at post time; it is never shown in the editable body.
6. Handle the outcome:
   - 201 → status `posted`, save `github_issue_number` and `github_issue_url`, return 200.
   - 410 → status `approved` with `last_error`; return 422 `ISSUES_UNAVAILABLE`.
   - Any other 4xx, a 5xx or a timeout → status `approved` with `last_error`; return the mapped error (502, 503 or 422). A retry is safe because of step 4.

The UI refetches the draft after any error.

## 13. Security requirements

| # | Requirement |
|---|---|
| S1 | Passwords are hashed with Argon2 (pwdlib), 8–128 characters. Passwords are never logged. |
| S2 | JWT HS256. `JWT_SECRET` is **required** in every environment and must be at least 32 characters unless `ENV=test`; otherwise startup fails. Claims: `sub` (the user id as a **string**), `ver` (`token_version`), `iat`, `exp` (`JWT_EXPIRE_MINUTES`, default 60). Decode with `algorithms=["HS256"]`, `options={"require": ["exp","iat","sub"]}` and `leeway=30`. On every request, load the user: inactive, missing or `ver` mismatch → 401. **Authorization always uses the role from the DB**, never a claim. |
| S3 | Login returns `INVALID_CREDENTIALS` for an unknown email, a wrong password and an inactive user alike. For unknown emails, verify against a dummy hash so response times match. |
| S4 | RBAC uses `require_role("admin")` on every mutating endpoint. A test matrix covers every endpoint × anonymous, viewer and admin. |
| S5 | SSRF: outbound HTTP goes only to the `GITHUB_API_URL` host and to `codeload.github.com` (tarballs only, after Location validation). The host allowlist is enforced in the client. URLs are never built from unvalidated input. |
| S6 | Secrets hygiene: `GITHUB_TOKEN`, `GITHUB_ISSUES_TOKEN`, `JWT_SECRET` and `METRICS_TOKEN` come only from env vars and are never logged or returned. The `httpx` and `httpcore` loggers are set to WARNING, because at INFO they log full URLs, including codeload `?token=` values. GitHub log lines and metric labels use **route templates** (`/repos/{owner}/{repo}/tarball/{ref}`), never real URLs. `.env.example` uses placeholders. |
| S7 | Secret values are never persisted: findings hold only path, line, pattern and confidence. Dependency details hold names only. Exception messages are never stored. **Test:** plant a real-looking key in a tarball fixture, plus a credential in a `requirements.txt` URL and a `package.json` URL, run a scan, and assert none of those strings appear in any DB row, API response, issue body or captured log record. |
| S8 | Nothing is posted to GitHub except through the `approve` endpoint, called by an admin. No background job ever posts. |
| S9 | Archive safety (§8.2): processing happens in memory only, only regular files are accepted, paths are normalised, and the download size, uncompressed size, file count and text budget are all capped. Repo content is never executed. |
| S10 | CORS allows only `CORS_ORIGINS`. |
| S11 | 500 responses return `INTERNAL_ERROR` with a request id and no stack trace; the stack trace goes to the logs. |
| S12 | Brute force: an in-process limiter allows 5 failed logins per (IP, email) per 15 minutes and 30 failed logins per IP per 15 minutes. The same 30-per-IP limit applies to `/auth/register`. Over the limit, return 429 `TOO_MANY_ATTEMPTS` with `Retry-After` **before** any password hashing. `X-Forwarded-For` is trusted only when `TRUST_PROXY=true`. |
| S13 | XSS: markdown (fix tips, issue preview) is rendered with react-markdown **without** rehype-raw, and only `http`, `https` and `mailto` links are allowed. Repo-derived strings are always rendered as text. nginx sends `Content-Security-Policy: default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'; connect-src 'self' <API origin>`, plus `X-Content-Type-Options: nosniff`. |
| S14 | Secure defaults: registration off, metrics off, `/docs` only in development, and docker-compose sets `ENV=production`. |

Known v1 trade-off: the JWT is kept in `localStorage`. This is acceptable for an internal tool given S13. Move to httpOnly cookies later if needed.

## 14. Reliability and observability

- **Logging:** JSON to stdout, at `LOG_LEVEL` (default INFO).
  - Each request logs `request_id`, method, route template, status and duration.
  - The response carries `X-Request-ID`.
  - Scan lifecycle events are logged as `scan_queued`, `scan_started`, `scan_completed`, `scan_failed` and `scan_orphaned`, each with `scan_id` and `repo_id`.
- **Metrics** (`/metrics`):
  - Disabled by default (`METRICS_ENABLED=false`, and the endpoint returns 404 when disabled).
  - When enabled, `METRICS_TOKEN` is required as `Authorization: Bearer`.
  - Labels use route templates only. The metrics are:
  ```
  gitwell_http_requests_total{method,route,status}
  gitwell_http_request_duration_seconds{method,route}        (histogram)
  gitwell_scans_total{status,trigger}
  gitwell_scan_duration_seconds                              (histogram)
  gitwell_rule_results_total{rule_id,status}
  gitwell_github_requests_total{endpoint,status}             (endpoint = route template)
  gitwell_github_rate_limit_remaining                        (gauge)
  ```
- **Scheduler:**
  - Configuration: APScheduler interval job every `SCHEDULER_TICK_MINUTES` (default 15), with `max_instances=1`, `coalesce=True` and `misfire_grace_time=60`.
  - Each tick:
    1. **Skip the whole tick** if `RateLimitState.reset_at` is in the future and `remaining < 50`.
    2. Select repos that have no scans, or whose latest scan (any status) has `created_at` older than `RESCAN_INTERVAL_HOURS`. Exclude repos with an active scan, and repos whose last 3 scans all failed with `REPO_NOT_FOUND` (unreachable).
    3. Order them never-scanned first, then by latest `created_at` ascending, ties broken by `repos.id`. Take at most `SCHEDULER_MAX_PER_TICK` (default 10).
    4. Insert a `scheduled` scan for each. If an insert hits the active-scan unique index, skip that repo silently.
- **Graceful shutdown** (lifespan):
  1. Stop the scheduler.
  2. Call `executor.shutdown(wait=False, cancel_futures=True)`.
  3. Mark the scans that were cancelled (still `queued`) as `failed`/`INTERRUPTED`.
  4. Set every running scan's deadline to now, so they abort within seconds.

  docker-compose sets `stop_grace_period: 20s`. Anything left over is recovered at the next startup (§8.1).

## 15. Frontend (dashboard)

The SPA lives in `frontend/`. It reads `VITE_API_BASE_URL` at **build time** (default `http://localhost:8000/api/v1`).

Design:
- Clean, minimal Tailwind styling, following the system light or dark theme via `prefers-color-scheme`. There is no toggle.
- Status colours: pass green, warn amber, fail red, N/A grey, error purple.
- Every list and page has loading, empty and error states.

| Route | Page | Contents |
|---|---|---|
| `/login` | Login | Form with inline errors. Shows a link to Register only if `GET /config` returns `allow_registration`. |
| `/register` | Register | Only if registration is enabled. On success, redirect to `/login` (no auto-login). |
| `/` | Dashboard | See the dashboard notes below the table. |
| `/repos/new` (modal is fine) | Add repo | URL input. The same regex as §12.1 runs client-side first, then the server's error is shown. On success, navigate to the repo page. |
| `/repos/:id` | Repo detail | See the repo detail notes below the table. |
| `/scans/:id` | Scan detail | The same rule-card view, for any scan. Failed scans show `error_message`. |
| `/issue-drafts/:id` | Draft review | See the draft review notes below the table. |
| `/users` | Users (admin) | User table with a role dropdown, an active toggle and a "Create user" form. Shows the server's `LAST_ADMIN` error inline. |
| `/account` | Account | Change password. |

**Dashboard (`/`):**
- Stat cards: repos tracked, average score, number of repos with grade F.
- A grade distribution bar.
- A "Most common failures" horizontal bar chart, built from `rule_failures`.
- A 30-day average-score trend line.
- A repos table with these columns:
  - name (a link)
  - score and grade badge, from `latest_completed_scan`. Show "Incomplete" if `rules_errored > 0` and "Partial" if `is_partial`.
  - Δ, from `score_delta`: ▲ green or ▼ red; "—" if null.
  - last scanned, as relative time from `latest_scan.created_at`.
  - a status chip: "Scanning…" if `active_scan_id`; "Last scan failed: {code}" if `latest_scan.status = failed`.
  - an Archived badge
  - admin actions: Scan now, and Delete (with a confirmation).
- Click a column header to sort. Admins see an "Add repository" button.

**Repo detail (`/repos/:id`):**
- Header: name, a GitHub link, the latest completed score and grade, the commit, and a "Scan now" button.
- A score-history line chart from `GET /repos/{id}/scans?status=completed&limit=200`, plotted in ascending `finished_at` order.
- Rule cards for the latest completed scan, from `GET /scans/{latest_completed_scan.id}`, **in API order**. Each card has a status badge, the message, an expandable fix tip (markdown), and a details view:
  - secrets: a table of `path:line — pattern (confidence)`
  - dependencies: the manifests table
  - tests: sample files
- Banners:
  - "Partial scan" when `is_partial`.
  - "Incomplete score" when `rules_errored > 0`.
  - A failed-scan banner when the latest scan failed.
  - For secrets results: "Git history is not scanned."
- A past-scans table; clicking a row opens the scan.
- A "Draft GitHub issue" button (admin). It is enabled only when `latest_completed_scan.has_findings` is true, `open_draft_id` is null, and the repo is neither archived nor without issues. Otherwise it is disabled with a tooltip giving the reason. If `open_draft_id` is set, show a link to that draft instead.
- A list of this repo's issue drafts.

**Draft review (`/issue-drafts/:id`):**
- While the draft is `drafted`, the title and markdown body are editable, with a textarea and a live preview.
- Buttons: Save, Discard, and **Approve & post**.
  - Approve & post is disabled when `github_token_configured` is false.
  - It opens a confirmation dialog: "This will create a **public** issue on owner/repo". Use "private" instead of "public" when `repo_is_private`.
- In `approved` status (after a failed post), show `last_error` with "Retry posting" and Discard buttons.
- In `posted` status, the page is read-only and links to the GitHub issue.

**Behaviour:**
- While a scan is active, poll `GET /scans/{id}` every 2 s. Stop at a terminal status and invalidate the repo queries.
- A 401 from any call clears the token and redirects to `/login`.
- Viewers never see admin controls; the server enforces this anyway.

## 16. Repository layout

```
GitWell/
├── backend/
│   ├── app/
│   │   ├── main.py            # app factory, middleware, routers, lifespan (recovery, scheduler, shutdown)
│   │   ├── config.py          # pydantic-settings (§19)
│   │   ├── db.py              # engine, pragmas, SessionLocal, Base + naming convention, UTCDateTime
│   │   ├── models/            # user.py, repo.py, scan.py, issue_draft.py
│   │   ├── schemas/           # Pydantic request/response models
│   │   ├── api/               # config.py, auth.py, users.py, rules.py, repos.py, scans.py, issue_drafts.py, dashboard.py, health.py, metrics.py
│   │   ├── core/              # security.py, deps.py, errors.py, logging.py, metrics.py, ratelimit.py
│   │   ├── services/          # repo_service.py, scan_service.py, issue_service.py, dashboard_service.py, user_service.py
│   │   ├── github/            # client.py, errors.py, url.py, ratelimit_state.py
│   │   ├── scanner/           # snapshot.py, archive_snapshot.py, api_snapshot.py, globbing.py, engine.py, scoring.py, worker.py, deadline.py
│   │   ├── rules/             # base.py, registry.py, secrets.py, readme.py, tests.py, ci.py, license.py, dependencies.py, gitignore.py, docs.py
│   │   └── scheduler.py
│   ├── migrations/            # alembic (render_as_batch=True)
│   ├── tests/                 # mirrors app/; conftest: in-memory SQLite (StaticPool), TestClient, FakeSnapshot, tarball builder, sync executor
│   ├── pyproject.toml
│   ├── uv.lock
│   └── Dockerfile
├── frontend/
│   ├── src/ (api/, pages/, components/, hooks/, lib/)
│   ├── package.json
│   ├── package-lock.json
│   ├── nginx.conf             # SPA fallback + security headers (S13)
│   └── Dockerfile
├── docs/
│   ├── RULES.md               # how to add a rule
│   └── DECISIONS.md
├── .github/workflows/ci.yml
├── docker-compose.yml
├── .env.example
├── .gitignore                 # includes .env, .env.*, !.env.example
├── CONTRIBUTING.md
├── LICENSE                    # MIT
└── README.md
```

**Dogfooding requirement:** GitWell's own repository MUST score **100** when scanned by GitWell. As a consequence:
- **Test fixtures MUST NOT contain literal secret-shaped strings.** Build them at runtime, for example `"AKIA" + "Q" * 16`, `"gh" + "p_" + "a" * 36`, and `"-----BEGIN " + "RSA PRIVATE KEY-----"`.
- `.env.example` uses placeholder values only.

## 17. Milestones (build order) and acceptance criteria

**M1: Skeleton**
- Layout from §16, `pyproject.toml` with `uv.lock`, and config for ruff, mypy and pytest.
- `config.py`, `db.py` (§6.1 in full), and an Alembic initial migration covering all tables and indexes.
- `GET /health` and `GET /config`.
- JSON logging, request-id middleware, the error-handler framework (§11.1), `pytest-socket` blocking network access, and `LICENSE`.
- ✅ Done when:
  - `pytest` passes.
  - `uvicorn app.main:app` starts and `/health` returns 200.
  - `alembic upgrade head` and then `downgrade base` work on SQLite and on Postgres (started with `docker run -e POSTGRES_PASSWORD=dev -p 5432:5432 postgres:16`).
  - A test proves that SQLite foreign keys cascade.

**M2: Auth and users**
- Login, me, change-password, the optional register endpoint, the users admin endpoints, bootstrap admin, `token_version`, `require_role`, the login rate limiter, and `LAST_ADMIN` rules.
- ✅ Done when tests cover:
  - Login → me.
  - Duplicate email → 409.
  - Wrong password, unknown email and inactive user each → 401 `INVALID_CREDENTIALS`.
  - A viewer gets 403 on every admin route (the RBAC matrix).
  - An expired token → 401.
  - Changing the password or role invalidates old tokens.
  - Demoting or deactivating the last admin → 409.
  - The 6th failed login → 429.
  - Register while disabled → 403.

**M3: GitHub client, snapshot and rule engine**
- The client: headers, redirect policy, host allowlist, retries, error classification, `RateLimitState`.
- The URL validator.
- `globbing.py`, `ArchiveSnapshot`, `ApiSnapshot` and the deadline.
- The rule base class and registry, all 8 rules, and scoring.
- ✅ Done when:
  - Each rule is tested for every status the spec defines for it, plus `error` (via a raising snapshot), using `FakeSnapshot(files={path: content})`.
  - The URL validator passes ≥ 20 table cases.
  - Glob matching is case-insensitive and `*` does not cross `/`. These tests run identically on Linux and Windows.
  - Client tests with respx cover: 200; 404; 403 with remaining 0; 429 with `Retry-After`; a secondary-limit body; 403 with an SSO header; 503 retried, then success; a timeout; a 301 rename on the API host being followed; a redirect to a foreign host being rejected.
  - Tarball tests:
    - a 302 to codeload is followed, codeload receives **no** Authorization header, and the `token=` value appears in no log record;
    - a malicious archive (`../evil`, absolute path, symlink, hardlink) produces only regular, safe files;
    - a gzip bomb (≥ 1 GB uncompressed of zeros) and a 200k-member archive both end with `is_partial`, bounded memory, and a completed snapshot.
  - Scoring tests: exclusions, null when there is nothing to count, null when `secrets` errors, `ruleset_version` stability.
  - Determinism: the same fixture scanned twice gives identical results.

**M4: Repos, scans and history**
- Repo CRUD, the scan trigger and worker (§6.1 transactions), startup recovery, the scan endpoints, `/rules`, `score_delta`, and `open_draft_id`.
- ✅ Done when:
  - An end-to-end test with mocked GitHub and an in-test tarball passes: add repo → the `on_add` scan completes → results are saved → re-scan using a **second fixture with a different score** → `score_delta` is correct.
  - Adding a renamed repo via its old URL → 409 (same `github_id`).
  - Directly inserting a `queued` scan, then POSTing a scan → 409 `SCAN_IN_PROGRESS`.
  - A raising rule produces an `error` result, the scan still completes, and `rules_errored = 1`.
  - Deleting a repo mid-scan leaves no orphan rows and raises no exception.
  - Empty repo → `REPO_EMPTY`. Rate limited → `GITHUB_RATE_LIMITED`.
  - The S7 secret-leak test passes.
  - An integration test runs the **real** `ThreadPoolExecutor` against a file-based SQLite DB with 2 concurrent scans, without errors.

**M5: Dashboard**
- `/dashboard/summary`, and the frontend: auth pages, dashboard, add repo, repo detail, scan detail, users, account. ESLint and Vitest set up.
- ✅ Done when:
  - API tests for the summary cover: an empty DB, null scores, the trend's baseline and carry-forward, and rules with 0 failures.
  - Vitest covers the URL validator and the grade and status badges.
  - `npm run build` passes with no TypeScript errors.
  - Manually: log in, add a real public repo, watch the scan complete, and see the rule cards, the history chart and the failures chart.

**M6: Issue drafting with approval**
- Draft generation (including the secrets case, mention escaping and truncation), edit, discard, approve and post (§12.3), and the draft review page.
- ✅ Done when tests cover:
  - The body lists every fail and warn rule in order and contains no secret paths; an `@` in a path is escaped.
  - A second draft → 409 `DRAFT_EXISTS`.
  - An archived repo → 422.
  - PATCH after approve → 409.
  - Two concurrent approves → exactly **one** POST (respx call count).
  - A POST timeout leaves the draft `approved` with `post_attempts = 1`. A retry finds the marker issue and adopts it **without** a second POST.
  - A 4xx leaves the draft `approved` with `last_error`.
  - Approving a `posted` draft → 409.
  - A draft left in `posting` at startup is recovered to `approved`.

**M7: Scheduler, hardening and delivery**
- The scheduler, metrics (with the token), graceful shutdown, Dockerfiles, docker-compose, the CI workflow, README, `.env.example`, `CONTRIBUTING.md` and `docs/RULES.md`.
- ✅ Done when:
  - Scheduler tests (with freezegun) show that it queues only stale and never-scanned repos, respects the per-tick maximum and the ordering, skips active, unreachable and rate-limited cases, and treats a unique-index conflict as a skip.
  - `docker compose up --build` gives a working stack.
  - CI is green.
  - Backend coverage is ≥ 85% overall and ≥ 95% for `app/rules`, `app/scanner` and `app/github`.
  - **The self-scan test** builds a `FakeSnapshot` from `git ls-files` of the repo itself, runs all rules and asserts `score == 100`.

## 18. Testing strategy

| Layer | What | Tools |
|---|---|---|
| Rules | Each rule × each status, plus edge cases: case-insensitive names, empty files, `read_text=None` with size > 0, nested directories, multiple manifests | pytest + `FakeSnapshot` |
| Scanner | Scoring, determinism, error isolation, deadline, tar safety and caps, API-mode prefetch | pytest; tarballs built in memory |
| GitHub client | Classification, retries, redirects, host allowlist, log redaction | respx, caplog |
| API | Auth, the RBAC matrix (every endpoint × anon, viewer and admin), validation, error shape, state machines, pagination and sorting | TestClient, in-memory SQLite (`StaticPool`) |
| Concurrency | Real executor with a file-based SQLite DB; concurrent approve | pytest, threads |
| Scheduler | Selection logic | freezegun |
| Frontend | URL validator, badges, markdown rendering without raw HTML | Vitest |

Rules for tests:
- Inject a synchronous executor for most API tests.
- Never sleep.
- The network is blocked by `pytest-socket`, and respx runs with `assert_all_mocked=True`.
- Secret-shaped strings are built at runtime (§16).

## 19. Configuration (env vars)

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `development`, `test` or `production`. docker-compose sets `production` |
| `DATABASE_URL` | `sqlite:///./gitwell.db` | Postgres: `postgresql+psycopg://user:pass@host:5432/gitwell` |
| `JWT_SECRET` | none | required; ≥ 32 chars unless `ENV=test` |
| `JWT_EXPIRE_MINUTES` | `60` | |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | none | bootstrap admin (§4) |
| `ALLOW_REGISTRATION` | `false` | |
| `TRUST_PROXY` | `false` | trust `X-Forwarded-For` for rate limiting |
| `GITHUB_TOKEN` | none | read access; optional for public repos |
| `GITHUB_ISSUES_TOKEN` | none | optional; used only to post issues |
| `GITHUB_API_URL` | `https://api.github.com` | overridden in tests only; GHES is out of scope |
| `SCAN_WORKERS` | `2` | |
| `SCAN_TIMEOUT_SECONDS` | `120` | |
| `MAX_ARCHIVE_MB` | `50` | compressed download cap |
| `MAX_UNCOMPRESSED_MB` | `500` | sum of tar member sizes |
| `MAX_FILES` | `100000` | |
| `MAX_FILE_BYTES` | `524288` | per-file content cap |
| `MAX_TEXT_TOTAL_MB` | `200` | total file content held in memory per scan |
| `API_SNAPSHOT_MAX_FETCHES` | `40` | |
| `SCHEDULER_ENABLED` | `true` | `false` in tests |
| `SCHEDULER_TICK_MINUTES` | `15` | |
| `RESCAN_INTERVAL_HOURS` | `24` | |
| `SCHEDULER_MAX_PER_TICK` | `10` | |
| `CORS_ORIGINS` | `http://localhost:5173` | comma-separated |
| `METRICS_ENABLED` | `false` | |
| `METRICS_TOKEN` | none | required when metrics are enabled |
| `LOG_LEVEL` | `INFO` | |

## 20. DevOps

- **Backend image:**
  - Built from `python:3.12-slim`, multi-stage, with dependencies installed by uv from `uv.lock`, running as a non-root user.
  - Entrypoint: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1`. Running the migration in the entrypoint is safe only because there is exactly one process (§6).
  - Healthcheck: `/health`.
- **Frontend image:** a node 20 build stage that takes `VITE_API_BASE_URL` as a build arg, then `nginx:alpine` serving `dist/` with SPA fallback and the S13 headers.
- **docker-compose.yml:**
  - `db`: postgres:16, with a volume and a `pg_isready` healthcheck.
  - `backend`: port `8000:8000`; `env_file: .env`; `ENV=production`; `CORS_ORIGINS=http://localhost:5173`; depends on db being healthy; `stop_grace_period: 20s`.
  - `frontend`: port `5173:80`, with build arg `VITE_API_BASE_URL=http://localhost:8000/api/v1`.
  - Everything starts with one command: `docker compose up --build`.
- **CI** (`.github/workflows/ci.yml`), on push and pull request:
  - **backend:** `ruff check`, `ruff format --check`, `mypy`, `pytest --cov` with the coverage gate.
  - **backend-postgres:** a Postgres service container; run `alembic upgrade head`, then `downgrade base`, then `upgrade head`, then the full test suite.
  - **frontend:** `npm ci`, lint, `tsc --noEmit`, `npm test`, `npm run build`.
  - **docker:** build both images.
  - Pin actions to major versions. The workflow sets `permissions: contents: read`.
- **README:** what GitWell is, a screenshot, a quick start (compose), local development, the env var table, token permissions (§12), "single process only", how to add a rule (link to `docs/RULES.md`), and known limitations (git history is not scanned; github.com only).

## 21. Success metrics

- Paste-to-completed-scan time: **p95 < 30 s** for repos under 50 MB.
- Deterministic results: two scans of the same commit with the same rule set are identical. This is tested, including on Windows and Linux runners.
- Zero secret values persisted anywhere (S7 test).
- Zero duplicate GitHub issues (M6 tests).
- Adding a rule touches exactly one source file and one test file.
- Adoption: the 30-day average score trend of tracked repos goes up.

## 22. Risks and mitigations

| Risk | Mitigation |
|---|---|
| GitHub rate limits | About 3 API calls per scan in archive mode; the scheduler skips ticks when the limit is low and caps scans per tick; clear 503 errors with `reset_at`; a gauge |
| Secret false positives | High, medium and low confidence; the `EXAMPLE` ignore; test-path downgrade; a placeholder list; `.env` files are medium only |
| Secret false negatives | The UI and message state "current tree only, history not scanned"; partial scans are flagged |
| Library repos unfairly penalised | A missing lock is a warn (not a fail) for pyproject, package.json and Cargo; the `.gitignore` `.env` check applies only to repos that use dotenv |
| Huge or malicious archives | Caps on compressed size, uncompressed size, file count and text budget; in-memory processing; regular files only |
| Duplicate GitHub issues | Atomic claim, the `posting` state, the marker comment with adoption on retry, no auto-retry of POST |
| Token leakage | Codeload gets no auth header; httpx logging off; route-template logging; catalog-only error messages |
| Score drift when rules change | `ruleset_version`; deltas across versions are null |
| Running two processes by accident | Documented single-process constraint, `--workers 1`, Recreate deploys |

## 23. Assumptions and decisions (beyond the original brief)
1. Rule statuses add `not_applicable` and `error` to the brief's pass, warn and fail, so scores stay fair and a crashing rule cannot break a scan.
2. Draft statuses add `posting` (needed for exactly-once posting) and `discarded`.
3. A React SPA was chosen over server-rendered pages.
4. Viewers are read-only.
5. Self-registration is off by default; admins create users.
6. Extra endpoints the UI needs: `/config`, `/auth/me`, `/auth/change-password`, `/users` (list, create, patch), `/rules`, repo get and delete, `/scans/{id}`, issue-draft list, get, patch and discard, and `/dashboard/summary`.
7. All API routes are under `/api/v1`, except `/health` and `/metrics`.
8. The brief's `.gitignore` rule ("excludes .env") is enforced only for repos that use dotenv, to avoid penalising Go, Rust and Java libraries.

## 24. Future (post-v1; do not build now)
- A DB-backed job queue and multi-replica support.
- Org-wide import; webhooks for scan-on-push.
- Git history secret scanning (for example via Gitleaks).
- Per-repo rule overrides.
- Slack and email digests; auto-fix PRs.
- A GitHub App instead of PATs; GitHub OAuth login.
- More rules: CODEOWNERS, branch protection, SECURITY.md, Dependabot config.

## 25. Review log
v2.0 incorporates two independent reviews of v1.0 (an implementer review and an architecture/security review). The main changes:
- Cooperative scan timeouts.
- In-memory tar processing with decompression-bomb caps.
- Codeload redirect handling without token leakage.
- No lazy fetching, so results are deterministic.
- Exactly-once issue posting.
- A DB-enforced single active scan.
- A single-process constraint.
- SQLite pragmas and worker session rules.
- `github_id`-based repo identity.
- A rate-limit classification that works.
- Regexes moved out of tables, and corrected regexes.
- Fairer `tests`, `dependencies` and `gitignore` rules.
- Confidence levels for secrets.
- Credential-free dependency details.
- Secure defaults.
- Login rate limiting.
- XSS and mention-injection protection.
- Dogfooding fixes (LICENSE, runtime-built test secrets, self-scan test).
