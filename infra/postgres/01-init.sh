#!/bin/sh
# 서비스별 DB·역할 분리 (NFR-06 최소 권한).
#   lakekeeper  : 카탈로그 메타데이터 DB 소유
#   dagster     : 오케스트레이터 실행 이력 DB 소유
#   aptlake     : 운영(ops)·API 메타(api) 스키마 — 스키마 소유자는 migrator, 앱 역할은 필요한 권한만
# 스키마 생성·권한 부여는 마이그레이션(api/migrations)이 담당하고, 여기서는 역할과 DB만 만든다.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE lakekeeper LOGIN PASSWORD '${LAKEKEEPER_DB_PASSWORD}';
CREATE DATABASE lakekeeper OWNER lakekeeper;

CREATE ROLE dagster LOGIN PASSWORD '${DAGSTER_DB_PASSWORD}';
CREATE DATABASE dagster OWNER dagster;

CREATE ROLE migrator LOGIN PASSWORD '${MIGRATOR_DB_PASSWORD}';
CREATE ROLE pipeline LOGIN PASSWORD '${PIPELINE_DB_PASSWORD}';
CREATE ROLE api_app  LOGIN PASSWORD '${API_DB_PASSWORD}';
CREATE DATABASE aptlake OWNER migrator;
REVOKE ALL ON DATABASE aptlake FROM PUBLIC;
GRANT CONNECT ON DATABASE aptlake TO pipeline, api_app;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname aptlake <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
