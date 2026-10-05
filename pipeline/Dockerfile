# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

# 빌드 단계: psycopg(C 구현)·psycopg2 를 시스템 libpq 에 대고 컴파일하고 dbt manifest 를 만든다
# (컴파일 도구는 최종 이미지에 남기지 않는다, QA-005)
FROM python:3.12-slim-trixie AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libpq-dev && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /opt/aptlake

# 의존성 레이어 (uv.lock 고정 — 공급망: 해시 검증된 잠금 파일로만 설치)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY dbt ./dbt
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# dbt manifest 는 이미지 빌드 시 생성 (실행 시 파싱 비용 제거, DB 연결 불필요)
RUN cd dbt && DBT_PROFILES_DIR=/opt/aptlake/dbt dbt parse --quiet

FROM python:3.12-slim-trixie
# OS 보안 업데이트를 빌드 때 적용(공식 이미지가 다시 빌드되기 전에도) + psycopg·psycopg2 가 쓰는 시스템 libpq (QA-005)
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH DAGSTER_HOME=/opt/dagster/home PYTHONUNBUFFERED=1 \
    DBT_PROJECT_DIR=/opt/aptlake/dbt
WORKDIR /opt/aptlake
# uv 는 실행 이미지에도 둔다 — make test-integration 이 컨테이너 안에서 uv pip 로 pytest 를 설치한다
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/aptlake /opt/aptlake

RUN useradd --uid 10001 --create-home app && mkdir -p /opt/dagster/home/storage \
    && chown -R app /opt/dagster /opt/aptlake/dbt
COPY --chown=app dagster_home/dagster.yaml dagster_home/workspace.yaml /opt/dagster/home/
USER app
EXPOSE 3000
CMD ["sh", "-c", "python -m aptlake_pipeline.startup && exec dagster dev -h 0.0.0.0 -p 3000 -w /opt/dagster/home/workspace.yaml"]
