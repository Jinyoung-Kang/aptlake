# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

# 빌드 단계: psycopg 의 C 구현을 시스템 libpq 에 대고 컴파일한다 (컴파일 도구는 최종 이미지에 남기지 않는다, QA-005)
FROM python:3.12-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libpq-dev && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /opt/api
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-trixie
# OS 보안 업데이트를 빌드 때 적용(공식 이미지가 다시 빌드되기 전에도) + psycopg 가 쓰는 시스템 libpq (QA-005)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 MIGRATIONS_DIR=/opt/api/migrations
WORKDIR /opt/api
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/api /opt/api
RUN useradd --uid 10001 --no-create-home app
USER app
EXPOSE 8610 8611
CMD ["uvicorn", "aptlake_api.main:create_public_app", "--factory", "--host", "0.0.0.0", "--port", "8610", \
     "--no-server-header", "--no-proxy-headers", "--log-level", "warning"]
