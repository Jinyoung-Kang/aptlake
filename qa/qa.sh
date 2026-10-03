#!/usr/bin/env bash
# QA 스택 명령 래퍼. 운영 스택(aptlake)에는 아무것도 하지 않는다.
#   qa/qa.sh env                 QA 전용 비밀값 qa/.env.qa 생성 (없을 때만, 값은 출력하지 않음)
#   qa/qa.sh up                  서빙 계층만 빌드·기동 (웹 3710 · API 8710 · 내부 8711)
#   qa/qa.sh down                QA 스택 중지 (볼륨 유지)
#   qa/qa.sh destroy             QA 스택과 QA 볼륨 삭제 (운영 볼륨 aptlake_* 는 이름이 달라 건드리지 않음)
#   qa/qa.sh <compose 인자...>   그 밖의 docker compose 명령 (예: qa/qa.sh ps, qa/qa.sh logs api)
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE=qa/.env.qa
SERVING=(postgres minio minio-init clickhouse ch-migrate redis db-migrate api api-internal web)
dc() { docker compose -p aptlake-qa -f docker-compose.yml -f qa/docker-compose.qa.yml --env-file "$ENV_FILE" "$@"; }

case "${1:-}" in
  env)
    [ -f "$ENV_FILE" ] && { echo "$ENV_FILE 이미 있음"; exit 0; }
    python3 qa/init_env_qa.py "$ENV_FILE" ;;
  up)
    [ -f "$ENV_FILE" ] || python3 qa/init_env_qa.py "$ENV_FILE"
    dc up -d --build "${SERVING[@]}" ;;
  down)
    dc --profile lake --profile obs stop ;;
  destroy)
    dc --profile lake --profile obs down -v --remove-orphans ;;
  *)
    dc "$@" ;;
esac
