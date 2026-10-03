# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim-trixie
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 MIGRATIONS_DIR=/opt/api/migrations
WORKDIR /opt/api
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
RUN useradd --uid 10001 --no-create-home app
USER app
EXPOSE 8610 8611
CMD ["uvicorn", "aptlake_api.main:create_public_app", "--factory", "--host", "0.0.0.0", "--port", "8610", \
     "--no-server-header", "--no-proxy-headers", "--log-level", "warning"]
