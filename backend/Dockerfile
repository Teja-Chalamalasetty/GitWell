# syntax=docker/dockerfile:1

# ---- build: install the locked dependencies into a virtualenv --------------------------------
FROM python:3.15-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.12.0
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Exactly what uv.lock says, production dependencies only.
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime ----------------------------------------------------------------------------------
FROM python:3.15-slim AS runtime
RUN useradd --system --uid 10001 --create-home gitwell
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER gitwell
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# Migrating in the entrypoint is safe ONLY because exactly one process runs (see the README):
# `--workers 1` is deliberate. `exec` makes uvicorn PID 1's child-free replacement, so it gets
# SIGTERM and shuts down gracefully (stop_grace_period in docker-compose.yml).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1"]
