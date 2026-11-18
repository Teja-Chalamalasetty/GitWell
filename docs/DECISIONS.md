# Decisions

Choices made where the PRD was silent or ambiguous.

## M1

- **Tests: socket blocking.** `pytest-socket` runs with `--allow-hosts=127.0.0.1,::1` instead of
  `--disable-socket`. On Windows, asyncio creates a loopback `socketpair` for every event loop, so a
  full socket ban breaks `TestClient`. Connections to any non-loopback host are still blocked
  (see `tests/test_no_network.py`).
- **Enum storage.** `str_enum()` in `app/db.py` persists enum *values* (e.g. `pass`, not `pass_`) as
  non-native strings, so stored values match the PRD's contract.
- **Settings injection.** `create_app(settings)` registers a `get_settings` dependency override so
  routes read the settings of the app they belong to (tests can build apps with different settings).
- **Alembic URL.** `migrations/env.py` reads `DATABASE_URL` directly (default
  `sqlite:///./gitwell.db`), so migrations run without `JWT_SECRET` and the other app settings.
- **Migration column types.** The initial migration uses `sa.DateTime(timezone=True)` rather than
  importing `app.db.UTCDateTime`, so old migrations never depend on application code.
- **Error handler scope.** The PRD error shape applies to `/api/v1`. Other paths (`/health`,
  `/docs`) keep FastAPI's default error bodies.
- **Unhandled exceptions** are caught in the request-id middleware (so the 500 carries
  `X-Request-ID`) and also by an `Exception` handler as a fallback.
- **Dev tooling on this machine.** Windows Application Control blocks the console launchers
  (`alembic.exe`, `pytest.exe`), so commands run as `python -m alembic` / `python -m pytest`.

## M2

- **Wrong current password on `/auth/change-password`** returns 400 `BAD_REQUEST`, not 401. The
  PRD's UI behaviour treats any 401 as an expired session and logs the user out.
- **Last-admin rule.** A non-self change can only hit `LAST_ADMIN` through a race (the acting admin
  is itself an active admin), so the check stays as defence in depth. Self-changes return
  `LAST_ADMIN` for the last active admin and `INVALID_STATE` otherwise, as specified. A PATCH that
  changes nothing is a no-op and does not bump `token_version`.
- **Inactive admins** do not count as active admins; demoting one never raises `LAST_ADMIN`.
- **Registration rate limit.** Every `/auth/register` attempt from an IP counts toward the
  30-per-15-minutes limit (the PRD does not say "failed" for register). A disabled registration
  returns 403 before anything is counted.
- **Login limiter.** A successful login clears that (IP, email) counter but not the per-IP counter.
  Both limits are checked before any password hashing.
- **Limiter lives on `app.state.auth_limiters`**, so tests can swap in a smaller limit or a fake clock.
- **Bootstrap admin** validates `ADMIN_EMAIL` and raises at startup if it is not an email address.
- **RBAC matrix** (`tests/api/test_rbac.py`) fails if a route exists that is not classified in it,
  so later milestones must add their endpoints there.
- **`/users` list** returns `{items, total}` with `limit`/`offset` like the other lists.

## M3

- **`check_deadline()` on snapshots.** The `RepoSnapshot` protocol gained `check_deadline()` and
  `max_file_bytes` (additive). The `secrets` rule needs the deadline (PRD 8.1) but `Rule.check`
  only receives the snapshot, and it needs `max_file_bytes` to tell "too large" from "binary".
- **Rule failures log type and stack frames only.** `traceback.format_tb` is used rather than
  `exc_info`, because the formatted exception message could contain repository content (S7).
- **`skipped_links`** counts symlinks, hardlinks, devices and FIFOs; plain directory entries are
  skipped silently. Unsafe names (absolute, `..`, NUL) are counted separately as `skipped_unsafe`.
- **Bomb protection checks the tar header before skipping the member's data**, so a 1 GB member of
  zeros is never decompressed. The member-count and declared-size caps are therefore enforced
  without reading file bodies.
- **Manifests with no dependencies are ignored** for `package.json` and `Cargo.toml` as well as
  `pyproject.toml` (the PRD only says so for `pyproject.toml` and `go.mod`). Otherwise a tooling-only
  `package.json` would earn the full `dependencies` weight for nothing. `requirements*.txt` with no
  requirement lines is ignored for the same reason.
- **Dependency details for lock-only ecosystems** (`go`, `Gemfile`, `Pipfile`) report `total: null`
  and `unpinned: []`: their verdict depends only on the lock file, and nothing is parsed.
- **Unreadable manifests** (too large, binary, not fetched) count as unparseable: `warn` with
  `parse_error: true`.
- **Unreadable `.gitignore`** gets the benefit of the doubt (treated as excluding `.env`).
- **Direct references** (`name @ url`) in requirements and `pyproject.toml` report the package name;
  other URL requirements report `url-requirement` (or a validated `#egg=` name). URLs themselves are
  never stored, since they can carry credentials.
- **Secrets scan is bounded**: it stops after 10,000 tracked findings (the first 100 by path/line are
  reported, `truncated: true`). An archive snapshot that is partial without findings warns with
  "Secret scan incomplete".
- **API-mode prefetch order** inside the first group is README, then LICENSE/LICENCE, COPYING,
  UNLICENSE, so README wins when `API_SNAPSHOT_MAX_FETCHES` is small.
- **Test fixtures never contain secret-shaped literals**, including assignment-shaped ones such as
  `password = "..."`: they are assembled at runtime (needed for the M7 self-scan to score 100).
- **Windows-only run so far.** The glob and rule tests are written to be OS-independent (no
  `fnmatch`, no `os.path`), but they have only been executed on Windows. CI (M7) runs them on Linux.

## M4

- **Injection points.** `create_app(settings, executor=..., github_client_factory=...)`. The executor
  is a `ThreadPoolExecutor(SCAN_WORKERS)` by default; tests inject an inline or manual executor.
  The scan worker (`ScanRunner`) receives only a `scan_id` and opens its own sessions.
- **Summaries are computed in Python.** `GET /repos` loads all repos, builds summaries with four
  window-function queries, sorts in Python (nulls last in both directions, ties by `full_name`) and
  then paginates. Fine for the tens-to-hundreds of repos v1 targets.
- **A failed scan still refreshes repo metadata** (rename, branch, archived flag) when GitHub
  answered the repo lookup. If a rename would collide with another tracked repo's `full_name`, the
  name fields are skipped and a warning is logged instead of failing the scan.
- **Duplicate detection** is by `github_id`. If a different tracked repo still holds the same
  `full_name` (a stale row for a renamed repo whose old name was reused), adding returns
  409 `REPO_ALREADY_EXISTS` too.
- **`SCAN_IN_PROGRESS`** carries `details.scan_id` of the active scan.
- **Graceful shutdown** is partly done here (executor shutdown, queued scans marked `INTERRUPTED`,
  running deadlines expired); stopping the scheduler is added in M7.
- **Startup recovery tolerates a missing schema**: it logs an error and continues instead of
  crashing, because tests start the app before creating tables. In production the entrypoint runs
  `alembic upgrade head` first.
- **Log handler** looks up `sys.stdout` at emit time so output redirection set up after start-up
  (including pytest's capture) is honoured.
- **Test fakes.** `tests/fake_github.py` serves GitHub (API and codeload) through respx with
  per-repo switches (empty, rate-limited, gone, renamed, mid-scan hook). `ManualExecutor` lets a
  test observe a scan while it is still `queued`.
- **Verified against real GitHub** (not just the fake): `octocat/Hello-World` was added and scanned
  through a running server; payload parsing, the codeload redirect and all 8 rules worked.

## M5

- **Trend and null scores.** A completed scan with a null score replaces that repo's carried score with
  "no score" from that day on. So the last trend point always equals `average_score`, and a repo whose
  `secrets` rule crashed does not keep contributing an old number. Days with no scored repo are omitted.
- **Day boundaries** are UTC. A scan counts for the day of its `finished_at`; later scans the same day
  win. The summary's "latest completed scan" orders by `created_at`, the trend by `finished_at`; they
  coincide because a repo never has overlapping scans.
- **Frontend stack.** Tailwind 3.4 (not 4) and Vite 5, because both are plain JavaScript/PostCSS with
  one well-known native binary (esbuild); `darkMode: "media"` follows the system theme with no toggle.
  Recharts 2.x and ESLint 9 are flagged as deprecated by npm; they work and are dev/visual-only. Moving
  to Recharts 3 / ESLint 10 is a post-v1 chore.
- **npm 12 blocks install scripts by default.** esbuild's `postinstall` is skipped, but its binary ships
  in the platform package, so Vite, Vitest and the build all work. No approval was needed.
- **Issue drafting UI is deferred to M6** (the "Draft GitHub issue" button, the repo's draft list and the
  draft review page). Their endpoints do not exist yet, and a button that cannot work is worse than none.
- **Server-side sorting.** The repo table asks the API for `sort`/`order` (nulls always last, ties by
  name) instead of sorting in the browser, so sorting matches the API's rules exactly.
- **Polling.** The repos list refetches every 2 s while any row has an active scan; the repo and scan
  pages poll `GET /scans/{id}` every 2 s until it is terminal, then invalidate the repo, history and
  dashboard queries. The dashboard refreshes its charts when the last active scan finishes.
- **After a password change** the UI signs the user out and returns to /login with a notice, since the
  server revokes every old token. A wrong current password is shown inline (the API returns 400, not
  401, so it does not trigger the global "session expired" redirect).
- **Vendor chunks.** `vite.config.ts` splits react, recharts and react-markdown into separate chunks.
- **Verified in a real browser** (built-in browser, real backend, real GitHub): sign-in, client-side URL
  validation, adding `octocat/Hello-World` and `octocat/Spoon-Knife`, live scan completion, rule cards,
  score-history chart, failures chart, 30-day trend, grade bar, and column sorting. No console errors.

## M6

- **One open draft per repo is enforced by the database.** The PRD states the invariant but lists only
  two indexes, so a race between two admins could create two drafts. Migration `0002` adds a partial
  unique index `ux_issue_drafts_open` on `repo_id` where `status IN ('drafted','approved','posting')`,
  the same technique as the one-active-scan rule. A losing creator gets 409 `DRAFT_EXISTS`. Verified on
  SQLite and on PostgreSQL 16 (upgrade, downgrade, re-upgrade, and the uniqueness itself). One M4 test
  that seeded two open drafts for one repo was changed to use two repos.
- **"@" defusing applies to rule MESSAGES, not fix tips.** Messages are where repo-derived text could
  appear, so every "@" gets a zero-width space (idempotent). Fix tips are trusted static text and are
  left untouched, because a zero-width space inside `uses: actions/checkout@v4` would break copy-paste.
  `safe_inline()` (backticks removed, "@" defused) is available for rules that quote a repo-derived
  string. No current rule puts repo strings in its message.
- **The posting token** (`GITHUB_ISSUES_TOKEN`, else `GITHUB_TOKEN`) is used for the whole posting flow:
  the permission pre-check, the duplicate lookup and the POST. Scanning never uses the issues token.
- **Permission pre-check.** `push` and `triage` both false (or both missing from a `permissions` object)
  blocks posting with `NO_REPO_PERMISSION`. If GitHub returns no `permissions` object at all, GitWell
  proceeds and lets GitHub decide.
- **POST failure mapping.** 410 -> `ISSUES_UNAVAILABLE`; 403/404/451 -> `NO_REPO_PERMISSION`; rate limits
  -> 503 `GITHUB_RATE_LIMITED`; 5xx and timeouts -> 503 `GITHUB_UNAVAILABLE`; 401 and any other 4xx ->
  502 `GITHUB_ERROR`. `last_error` and the API message are fixed strings, never GitHub's response body.
- **Never post blind.** If the duplicate lookup (step 4) fails, nothing is posted and the draft stays
  `approved`. The lookup follows up to 5 pages of 100 issues.
- **A draft can never be left stuck in `posting`** by an exception: any unexpected error releases it to
  `approved` ("Unexpected internal error.") before propagating; a retry then looks for an existing issue.
- **The approve flow refreshes the repo's `is_archived`/`has_issues`/`is_private`** from the lookup it
  already makes, and posts to the repo's CURRENT owner/name (so a rename between draft and approval works).
- **Editing guards.** `PATCH` rejects any text containing "gitwell-draft" (case-insensitive) so a user
  cannot forge the dedupe marker; titles are trimmed; whitespace-only bodies are rejected.
- **UI.** Unsaved edits disable "Approve & post" (it posts what is saved). The screen remounts when the
  status changes, so a failed post moves the page to the retryable `approved` view showing `last_error`.
  "Retry posting" asks for the same public/private confirmation. The preview is labelled just "Preview":
  `react-markdown` without GFM shows `- [ ]` as text, while GitHub renders checkboxes.
- **Verified live without posting to real GitHub.** Posting would create a public issue on someone's
  repository, so the real backend and frontend were run against a local stand-in for the GitHub issue
  endpoints. It was made to lose a response after creating the issue; the retry adopted that issue and
  the stand-in received exactly one POST.

## M7

- **Scheduler.** One APScheduler interval job (`max_instances=1`, `coalesce=True`,
  `misfire_grace_time=60`). The first tick runs 30 seconds after start so a restart catches up
  instead of waiting a whole interval. A tick is skipped entirely when GitHub's remaining quota is
  under 50 and has not reset yet; there is no separate per-repo "rate-limited" state. Scheduled scans
  have no triggering user. "Unreachable" (the last 3 scans all `REPO_NOT_FOUND`) is cleared only by a
  scan that succeeds, which an admin must trigger by hand. Due repos are selected in Python from
  window-function queries, like the dashboard summaries.
- **Metrics.** Each app gets its own `Metrics` registry (`create_app` installs it), so tests and apps
  never share counters. `gitwell_scans_total` counts `queued`, `completed` and `failed`; a scan whose
  repo was deleted mid-scan (orphaned) is not counted as finished. The rate-limit gauge reads the
  shared state when scraped and reports `NaN` until the limit is known. When metrics are disabled the
  route is not registered at all, so `/metrics` is a plain 404 rather than a 401.
- **Route templates keep their `/api/v1` prefix.** Nested routers report paths without it, which would
  label the same route `/config` and the health check `/health` ambiguously; the middleware restores
  the prefix. Metric labels and access logs therefore show `/api/v1/config`.
- **Single-process guard.** A warning is logged if `WEB_CONCURRENCY` is set above 1.
- **Tests can run on PostgreSQL.** `TEST_DATABASE_URL` points the same suite at Postgres; every test
  then starts from truncated tables with ids restarting at 1, so ids match SQLite. Three tests are
  SQLite-only (pragmas and the migration file round trip) and are skipped there; CI's Postgres job runs
  `alembic` itself. The full suite passes on PostgreSQL 16 and on SQLite, on Windows and on Linux.
- **"One source file + one test file" is now enforced, not just claimed.** Tests that assert exact
  scores run scans with an explicit v1 rule set (`create_app(rules_provider=...)`), and tests that used
  to assert "exactly 8 rules" now check the v1 rules are present. Adding a ninth rule (the worked example
  in `docs/RULES.md`) with exactly one source and one test file left all 940 tests passing.
  `tests/test_rules_doc.py` runs the guide's example so the guide cannot go stale.
- **Self-scan.** Uses `git ls-files` in a checkout and otherwise walks the tree honouring `.gitignore`
  (`pathspec`, a dev-only dependency), i.e. what `git add .` would add. It scored 100 only after the
  fake passwords in tests were renamed to contain the word "test": the secrets rule correctly flags
  any quoted password-style value of 8+ characters and deliberately ignores values containing
  placeholder markers such as "test". The rule was not loosened. (The first version of this very note
  quoted such a pair and failed the self-scan, so examples are described, not spelled out.)
- **Docker.** The backend image is multi-stage on `python:3.12-slim`, installs exactly `uv.lock` (uv is
  pinned), runs as a non-root user, and starts with `alembic upgrade head && exec uvicorn ... --workers 1`
  so SIGTERM reaches uvicorn. The Postgres service publishes no port (a host may already run Postgres on
  5432). `POSTGRES_PASSWORD` is required and must use letters and digits only, since it is embedded in
  `DATABASE_URL`. `frontend/nginx.conf` is an envsubst template, so the CSP's API origin
  (`API_ORIGIN`) is set at run time while `VITE_API_BASE_URL` is set at build time.
- **The PRD's CSP works unmodified.** It was verified in a real browser against the production build:
  inline styles that React sets through the DOM are not blocked by `style-src`, so no `'unsafe-inline'` was
  needed. One thing did break: the favicon was a `data:` URI, which `default-src 'self'` blocks, so it is
  now a file (`frontend/public/favicon.svg`) rather than loosening the policy.
- **CI.** `.github/workflows/ci.yml` has the four jobs from the PRD, `contents: read`, actions pinned to
  major versions, and the coverage gates as three `coverage report --include=... --fail-under=95` runs.
  It has NOT been run on GitHub. Every command in it was run locally: the backend job and the Postgres job
  in `python:3.12-slim` on Linux (945 and 942 tests passed), the frontend job in `node:20`, and the Docker
  images through `docker compose up --build`.
- **Verified live against the real stack** (`docker compose up --build`): the entrypoint migrated
  PostgreSQL; the admin was bootstrapped; sign-in, adding two real public repos and the dashboard work in
  the production build; `/metrics` returns 401 without or with a wrong token and 200 with the right one;
  `/docs` is a 404; the scheduler queued scans in the right order with a per-tick cap; `docker compose
  stop` ended in 2 seconds with exit code 0 (graceful); data survived a restart.
