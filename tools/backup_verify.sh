#!/usr/bin/env bash
# 백업 복원 확인 (make backup-verify): 가장 최근 덤프를 임시 PostgreSQL 컨테이너에 실제로 복원하고
# 핵심 표의 행 수를 운영 DB 와 비교한다. MinIO 사본은 객체 수가 운영 이상인지 본다. 운영 데이터는 건드리지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-backups}"
LAST="$(ls -1d "$DEST"/pg/*/ 2>/dev/null | sort | tail -1)"
[ -n "$LAST" ] || { echo "백업이 없습니다: make backup" >&2; exit 1; }
NAME="aptlake-restore-check-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=restorecheck -v "$PWD/$LAST:/dump:ro" postgres:16-alpine >/dev/null
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
fail=0
for db in aptlake lakekeeper dagster; do
  docker exec "$NAME" createdb -U postgres "$db"
  docker exec "$NAME" pg_restore -U postgres -d "$db" --no-owner --no-privileges --exit-on-error "/dump/$db.dump"
done

check() {  # DB 질의 — 복원본과 운영의 값이 같아야 한다
  local db="$1" sql="$2"
  local got live
  got="$(docker exec "$NAME" psql -U postgres -d "$db" -Atc "$sql")"
  live="$(docker compose exec -T postgres psql -U postgres -d "$db" -Atc "$sql")"
  if [ "$got" = "$live" ]; then echo "  OK   $db: $sql = $got"; else echo "  DIFF $db: $sql 복원 $got / 운영 $live"; fail=1; fi
}
echo "복원본 비교 ($LAST):"
check aptlake "SELECT count(*) FROM api.client"
check aptlake "SELECT count(*) FROM api.api_key"
check aptlake "SELECT count(*) FROM ops.ingest_partition"
check aptlake "SELECT max(version) FROM ops.dataset_version"
check lakekeeper "SELECT count(*) FROM tabular"
# 감사 로그는 백업 뒤에도 쌓이므로 '복원본 ≤ 운영'
a_bak="$(docker exec "$NAME" psql -U postgres -d aptlake -Atc 'SELECT count(*) FROM api.audit_log')"
a_live="$(docker compose exec -T postgres psql -U postgres -d aptlake -Atc 'SELECT count(*) FROM api.audit_log')"
[ "$a_bak" -le "$a_live" ] && echo "  OK   감사 로그 복원 $a_bak ≤ 운영 $a_live" || { echo "  DIFF 감사 로그"; fail=1; }

for b in raw lake; do
  bak="$(find "$DEST/minio/$b" -type f | wc -l | tr -d ' ')"
  live="$(docker compose run --rm --no-deps -T --entrypoint sh minio-init -c \
    "mc alias set local http://minio:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null && mc ls --recursive local/$b | wc -l" 2>/dev/null | tail -1 | tr -d ' ')"
  if [ "$bak" -ge "$live" ]; then echo "  OK   minio $b: 사본 $bak ≥ 운영 $live 객체"; else echo "  DIFF minio $b: 사본 $bak < 운영 $live"; fail=1; fi
done
[ "$fail" = 0 ] && echo "백업 복원 확인 통과" || { echo "백업 복원 확인 실패" >&2; exit 1; }
