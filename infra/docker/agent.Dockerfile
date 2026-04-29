# syntax=docker/dockerfile:1
# ── builder: install deps with uv ────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY apps/agent/pyproject.toml apps/agent/uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project

# ── runner: lean image with venv + app code only ──────────────────────────────
FROM python:3.11-slim AS runner
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY --from=builder /build/.venv ./.venv
COPY apps/agent/app      ./app
COPY infra/db/migrations ./migrations
COPY infra/db/alembic.ini ./

EXPOSE 8000
# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
