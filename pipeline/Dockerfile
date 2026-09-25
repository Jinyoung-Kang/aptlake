# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.19 AS uv

FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH DAGSTER_HOME=/opt/dagster/home PYTHONUNBUFFERED=1 \
    DBT_PROJECT_DIR=/opt/aptlake/dbt
WORKDIR /opt/aptlake

# 의존성 레이어 (uv.lock 고정 — 공급망: 해시 검증된 잠금 파일로만 설치)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY dbt ./dbt
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# dbt manifest 는 이미지 빌드 시 생성 (실행 시 파싱 비용 제거, DB 연결 불필요)
RUN cd dbt && DBT_PROFILES_DIR=/opt/aptlake/dbt dbt parse --quiet

RUN useradd --uid 10001 --create-home app && mkdir -p /opt/dagster/home/storage \
    && chown -R app /opt/dagster /opt/aptlake/dbt
COPY --chown=app dagster_home/dagster.yaml dagster_home/workspace.yaml /opt/dagster/home/
USER app
EXPOSE 3000
CMD ["sh", "-c", "python -m aptlake_pipeline.startup && exec dagster dev -h 0.0.0.0 -p 3000 -w /opt/dagster/home/workspace.yaml"]
