# Contributing to GitWell

Thanks for helping. This guide covers setting up, the checks every change must pass, and how we
work. If something here is wrong or missing, fixing it is a welcome contribution too.

## Dev setup

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, and (optionally) Docker for
PostgreSQL and the full stack.

```bash
# Backend
cd backend
uv sync                      # installs everything from uv.lock
cp ../.env.example ../.env   # then set JWT_SECRET (>= 32 chars), ADMIN_EMAIL, ADMIN_PASSWORD
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --workers 1

# Frontend (in another terminal)
cd frontend
npm ci
npm run dev                  # http://localhost:5173, talks to http://localhost:8000/api/v1
```

GitWell is a **single-process** service: never run more than one uvicorn worker or backend
replica (see the README).

## Checks that must pass

Run these before you open a pull request. CI runs exactly the same commands.

```bash
# Backend (from backend/)
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest --cov=app

# Frontend (from frontend/)
npm run lint
npm run typecheck
npm test
npm run build
```

Coverage gates: at least **85%** overall and at least **95%** for each of `app/rules`,
`app/scanner` and `app/github`. `mypy` is strict on those three packages.

### Testing against PostgreSQL

The tests use in-memory SQLite by default. Production uses PostgreSQL, so CI also runs the whole
suite on it. To do the same locally:

```bash
docker run -d --name gw-pg -e POSTGRES_PASSWORD=dev -e POSTGRES_DB=gitwell_test -p 5433:5432 postgres:16
TEST_DATABASE_URL=postgresql+psycopg://postgres:dev@localhost:5433/gitwell_test uv run pytest
```

Each test starts from empty tables, so the suite can be pointed at any scratch database.

## Ground rules for tests

- **No real network calls.** Every GitHub call is mocked (`respx`); `pytest-socket` blocks the rest.
- **Never sleep.** Inject a clock or an executor instead (`freezegun`, `InlineExecutor`).
- **Never write a secret-shaped string literal** in code, fixtures, docs or test data. GitWell scans
  its own repository and must score 100, and the secrets rule would (rightly) flag it. Build such
  values at runtime, for example `"AKIA" + "Q" * 16`, and assemble assignment-shaped text such as
  `password = "..."` the same way.
- Prove a guarantee by breaking it: if you add a safeguard, check that a test fails when the
  safeguard is removed.

## Adding or changing a rule

A new rule is **one source file plus its test file**. See [docs/RULES.md](docs/RULES.md).

## Workflow

1. Open an issue for anything non-trivial, so the approach can be agreed first.
2. Branch from `main`, keep the change focused, and add or update tests with it.
3. Run the checks above. Update `docs/` and the README when behaviour changes.
4. Open a pull request that says what changed and why. Small pull requests get reviewed faster.

Where the specification is silent, choose the simplest option that fits the rest of the design and
record it in [docs/DECISIONS.md](docs/DECISIONS.md) with the reason.

## Code style

- Python: formatted and linted by `ruff` (line length 100), typed with `mypy`. Prefer small,
  explicit functions; keep database access in `app/services`, HTTP in `app/api`.
- TypeScript: `eslint` and `tsc --strict`; Tailwind for styling; no `any` without a comment.
- Error messages shown to users come from fixed catalogs, never from exception text or GitHub
  response bodies. Repository content (paths, package names) is data: render it as text, never as
  markup, and never log or store secret values.

## Security

Please do not open a public issue for a vulnerability. Contact a maintainer privately and give them
time to fix it before disclosure.
