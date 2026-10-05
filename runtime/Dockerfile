# syntax=docker/dockerfile:1.7

ARG APP_DIR=/usr/local/src/app

# ---------- builder ----------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ARG APP_DIR
WORKDIR ${APP_DIR}

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------- runtime (no uv) ----------
FROM python:3.13-slim-bookworm AS runtime
ARG APP_DIR
ENV APP_DIR=${APP_DIR} \
    PATH="${APP_DIR}/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR ${APP_DIR}

RUN groupadd --system app \
 && useradd --system --gid app --home-dir ${APP_DIR} --shell /usr/sbin/nologin app \
 && chown app:app ${APP_DIR}

COPY --from=builder --chown=app:app ${APP_DIR}/.venv ${APP_DIR}/.venv
COPY --chown=app:app smoke_test.py ./

USER app

# Extensions baked in (~/.duckdb/extensions): VMs have no external IP, and
# httpfs is deliberately absent so gs:// is always served by the gcs extension.
RUN python -c "import duckdb; c = duckdb.connect(); c.sql('INSTALL gcs FROM community'); c.sql('INSTALL tpch')"

ENTRYPOINT ["python", "-m", "duckless_runtime"]
CMD ["--help"]
