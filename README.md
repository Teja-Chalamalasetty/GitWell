# GitWell

[![CI](https://github.com/Teja-Chalamalasetty/GitWell/actions/workflows/ci.yml/badge.svg)](https://github.com/Teja-Chalamalasetty/GitWell/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)
![React 18](https://img.shields.io/badge/react-18-61DAFB.svg)

GitWell scans GitHub repositories against an engineering-quality checklist and shows how healthy
they are, and whether they are getting better.

For every repository you add, it checks for a README, a license, tests, CI, committed secrets,
pinned dependencies, a sensible `.gitignore` and contributor docs. Each repo gets a **0-100 score**,
a per-rule breakdown, and a concrete fix for every failure. Scans are stored, so a dashboard can
show all repos side by side, the most common failures, and the 30-day trend. For repos that fail
checks, GitWell drafts a GitHub issue that an administrator reviews, edits and approves.

**Nothing is ever posted to GitHub without an explicit human approval**, and each approved draft is
posted exactly once.

![The GitWell dashboard](docs/screenshot.jpg)

## Quick start

You need Docker with Compose.

```bash
cp .env.example .env
# Edit .env: set JWT_SECRET (32+ characters), POSTGRES_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD,
# and a GITHUB_TOKEN (see "GitHub tokens" below).
docker compose up --build
```

Open <http://localhost:5173> and sign in with the admin account from `.env`. Add a repository by
pasting its URL; the first scan starts immediately.

The API is at <http://localhost:8000/api/v1> and a health check at `/health`. To stop everything,
run `docker compose down`; add `-v` to also delete the database volume.

## Local development

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `uv sync` and `uv run uvicorn app.main:app --workers 1`
in `backend/`, and `npm ci && npm run dev` in `frontend/`. The interactive API docs at `/docs` are
served only when `ENV=development`.

## Configuration

Everything is configured through environment variables (a `.env` file works for local use and for
`docker compose`). Values marked required have no default.

| Variable | Default | Notes |
|---|---|---|
| `ENV` | `development` | `development`, `test` or `production`. docker-compose sets `production`. |
| `DATABASE_URL` | `sqlite:///./gitwell.db` | Postgres: `postgresql+psycopg://user:pass@host:5432/gitwell`. |
| `POSTGRES_PASSWORD` | none | Used by `docker-compose.yml` for its Postgres container. |
| `JWT_SECRET` | none, **required** | At least 32 characters. |
| `JWT_EXPIRE_MINUTES` | `60` | Login session length. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | none | Bootstrap admin, created at startup if the email is new. Password 8+ characters. |
| `ALLOW_REGISTRATION` | `false` | When on, self-registered users are always viewers. |
| `TRUST_PROXY` | `false` | Trust `X-Forwarded-For` for login rate limiting. |
| `GITHUB_TOKEN` | none | Read access. Optional for public repos. |
| `GITHUB_ISSUES_TOKEN` | none | Used only to post issues. Falls back to `GITHUB_TOKEN`. |
| `GITHUB_API_URL` | `https://api.github.com` | github.com only; GitHub Enterprise Server is not supported. |
| `SCAN_WORKERS` | `2` | Concurrent scans. |
| `SCAN_TIMEOUT_SECONDS` | `120` | Per-scan time limit. |
| `MAX_ARCHIVE_MB` | `50` | Compressed download cap. Larger repos fall back to API mode (needs a token). |
| `MAX_UNCOMPRESSED_MB` | `500` | Sum of file sizes in the archive. |
| `MAX_FILES` | `100000` | Maximum files read from an archive. |
| `MAX_FILE_BYTES` | `524288` | Per-file content cap. |
| `MAX_TEXT_TOTAL_MB` | `200` | Total file content held in memory per scan. |
| `API_SNAPSHOT_MAX_FETCHES` | `40` | Files fetched in API mode. |
| `SCHEDULER_ENABLED` | `true` | Re-scan repos automatically. |
| `SCHEDULER_TICK_MINUTES` | `15` | How often the scheduler looks for due repos. |
| `RESCAN_INTERVAL_HOURS` | `24` | A repo is re-scanned when its latest scan is older than this. |
| `SCHEDULER_MAX_PER_TICK` | `10` | Scans queued per tick. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated. |
| `METRICS_ENABLED` | `false` | When off, `/metrics` does not exist (404). |
| `METRICS_TOKEN` | none | Required when metrics are on; sent as `Authorization: Bearer <token>`. |
| `LOG_LEVEL` | `INFO` | Logs are JSON on stdout. |

The frontend reads `VITE_API_BASE_URL` **at build time** (default `http://localhost:8000/api/v1`);
docker-compose passes it as a build argument.

## GitHub tokens

GitWell works without a token for small public repositories, but anonymous GitHub access is limited
to 60 requests per hour and cannot scan large repositories, so configure one.

- **`GITHUB_TOKEN`** (reading): a fine-grained personal access token with **Contents: read** and
  **Metadata: read**, or a classic token with `repo` (private repos) or `public_repo`.
- **`GITHUB_ISSUES_TOKEN`** (optional, posting issues only): **Issues: read & write**. If unset,
  `GITHUB_TOKEN` is used. Using a separate token lets you keep the scanning token read-only.

A fine-grained token covers a **single resource owner** (one user or one organisation), so a
deployment that tracks repos from several owners needs a classic token or one deployment per owner.
A GitHub App would remove this limit and is a post-v1 item.

Tokens are read only from the environment and are never logged or returned by the API.

## Run exactly one process

GitWell v1 is a **single-process** service: run one container, one replica, and
`uvicorn --workers 1` (the Dockerfile does). Startup recovery, the scan executor, the scheduler and
the login rate limiter all live in that one process, and running two would double-run scans and
duplicate scheduled work. If you deploy on Kubernetes, use one replica with the `Recreate` strategy
so old and new pods never overlap. GitWell logs a warning if `WEB_CONCURRENCY` is greater than 1.

## How scoring works

Each rule returns pass, warn or fail with a message and a fix tip. The score is the weighted share
of passing rules (a warning counts half), rounded, and is deterministic: the same commit and the
same rule set always give the same result.

| Rule | Weight | Checks |
|---|---|---|
| No committed secrets | 20 | API keys, tokens and private keys in the current tree |
| README | 15 | A README that is not trivially short |
| Tests | 15 | Test files or test directories |
| Continuous integration | 15 | A CI configuration |
| License | 10 | A LICENSE file |
| Pinned dependencies | 10 | Lock files or pinned versions |
| `.gitignore` | 10 | A `.gitignore` that keeps `.env` files out |
| Contributor docs | 5 | CONTRIBUTING or a `docs/` folder |

Grades: A 90+, B 75+, C 60+, D 40+, F below 40. Secret findings show only the file and line, never
the value. Adding a rule is a one-file change: see [docs/RULES.md](docs/RULES.md).

## Operations

- `GET /health` reports the database state and never calls GitHub.
- Set `METRICS_ENABLED=true` and `METRICS_TOKEN` to expose Prometheus metrics at `/metrics`
  (HTTP, scans, rule results, GitHub requests and the GitHub rate-limit gauge). Labels are route
  templates only.
- On shutdown GitWell stops the scheduler, stops accepting scans, marks scans that never started as
  interrupted, and tells running scans to abort. Anything left over is recovered at the next start.
- The product specification is in [docs/PRD.md](docs/PRD.md). Decisions made where it was silent
  are recorded in [docs/DECISIONS.md](docs/DECISIONS.md).

## Known limitations

- Only the **current tree of the default branch** is scanned. Git history is **not** scanned for
  secrets, and the UI says so. A deleted secret may still be in history: rotate it.
- github.com only: no GitLab, Bitbucket or GitHub Enterprise Server.
- Repositories are added one at a time; there is no organisation-wide import and no webhooks.
- One global list of repositories shared by all users; no teams, and no per-repo rule settings.
- Sign-in is by email and password only (no GitHub OAuth, email verification or 2FA), and the session
  token lives in the browser's local storage.
- GitWell reports problems; it does not open pull requests or fix repositories.

## License

[MIT](LICENSE)
