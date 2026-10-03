#!/usr/bin/env bash
# 복원 훈련: 백업을 '새 장비'에 복원한다 — 운영·QA 스택의 컨테이너·볼륨은 건드리지 않는다.
#   qa/probes/restore_drill.sh <백업 폴더 BACKUP_DIR> <env 파일(백업의 env 와 같은 비밀값)>
# runbook '복원' 절차를 따른다:
#   1) 새 PostgreSQL 이 infra/postgres/01-init.sh 로 역할·DB(소유자·권한)를 만든다
#   2) 그 DB 에 pg_restore --clean --if-exists (DB 를 지우고 다시 만들지 않음)
#   3) 새 MinIO 에 mc mirror
# 확인: 핵심 행 수가 manifest 와 같고, api_app·pipeline 역할로 실제 읽기·쓰기가 되며(권한 유지), 객체 목록이 같다.
# 임시 컨테이너는 --rm 이라 멈추면 사라지고, 데이터는 익명 볼륨이라 함께 사라진다.
set -euo pipefail
cd "$(dirname "$0")/../.."
BK="$1"; ENVF="$2"
case "$BK" in /*) ;; *) BK="$PWD/$BK" ;; esac   # 도커 볼륨은 절대 경로
LAST="$(ls -1d "$BK"/pg/*/ | grep -v '\.partial/$' | sort | tail -1)"
NET="aptlake-restore-drill-$$"; PG="restore-drill-pg-$$"; S3="restore-drill-minio-$$"
val() { sed -n "s/^$1=//p" "$ENVF"; }
cleanup() { docker stop "$PG" "$S3" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$NET" >/dev/null
docker run -d --rm --name "$PG" --network "$NET" \
  -e POSTGRES_PASSWORD="$(val POSTGRES_PASSWORD)" -e LAKEKEEPER_DB_PASSWORD="$(val LAKEKEEPER_DB_PASSWORD)" \
  -e DAGSTER_DB_PASSWORD="$(val DAGSTER_DB_PASSWORD)" -e MIGRATOR_DB_PASSWORD="$(val MIGRATOR_DB_PASSWORD)" \
  -e PIPELINE_DB_PASSWORD="$(val PIPELINE_DB_PASSWORD)" -e API_DB_PASSWORD="$(val API_DB_PASSWORD)" \
  -v "$PWD/infra/postgres/01-init.sh:/docker-entrypoint-initdb.d/01-init.sh:ro" -v "$LAST:/dump:ro" postgres:16-alpine >/dev/null
until docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; do sleep 1; done
sleep 3
echo "백업: $LAST"
for db in aptlake lakekeeper dagster; do
  docker exec "$PG" pg_restore -U postgres -d "$db" --clean --if-exists "/dump/$db.dump" && echo "  pg_restore $db: 성공" || echo "  pg_restore $db: 실패(exit $?)"
done
fail=0
while IFS='|' read -r db sql want; do
  [ "$db" = clickhouse ] && continue
  got="$(docker exec "$PG" psql -U postgres -d "$db" -Atc "$sql")"
  if [ "$got" = "$want" ]; then echo "  OK   $db: $sql = $got"; else echo "  DIFF $db: $sql 복원 $got / 백업 $want"; fail=1; fi
done < "$LAST/manifest.txt"
as() {  # $1 역할 $2 비밀번호 변수 $3 질의 — 역할 계정으로 접속해 권한이 살아 있는지
  docker exec -e PGPASSWORD="$(val "$2")" "$PG" psql -h 127.0.0.1 -U "$1" -d aptlake -qAtc "$3" 2>&1 | tail -1 || true  # 권한 거부가 기대값인 확인도 있다
}
r1="$(as api_app API_DB_PASSWORD 'SELECT count(*) FROM api.api_key')"
r2="$(as api_app API_DB_PASSWORD "UPDATE api.api_key SET last_used_at = last_used_at WHERE false RETURNING 1")"
r3="$(as pipeline PIPELINE_DB_PASSWORD "INSERT INTO ops.api_budget (day, source, limit_calls) VALUES ('2000-01-01','drill',1) ON CONFLICT DO NOTHING RETURNING 'ok'")"
r4="$(as api_app API_DB_PASSWORD "DELETE FROM api.api_key WHERE false")"
echo "  권한: api_app 키 읽기=$r1 · api_app 키 갱신=${r2:-(0행 성공)} · pipeline ops 쓰기=$r3 · api_app 키 삭제(권한 없어야)=$r4"
case "$r1" in ''|*ERROR*) fail=1 ;; esac
case "$r3" in ok) ;; *) fail=1 ;; esac
case "$r4" in *"permission denied"*) ;; *) fail=1 ;; esac

docker run -d --rm --name "$S3" --network "$NET" -e MINIO_ROOT_USER=root -e MINIO_ROOT_PASSWORD="$(val MINIO_ROOT_PASSWORD)" \
  pgsty/silo:RELEASE.2026-09-16T00-00-00Z server /data >/dev/null
for b in raw lake; do
  got="$(docker run --rm --network "$NET" -v "$BK/minio/$b:/backup/$b:ro" -e P="$(val MINIO_ROOT_PASSWORD)" --entrypoint sh \
    pgsty/silo:RELEASE.2026-09-16T00-00-00Z -c "until mc alias set d http://$S3:9000 root \"\$P\" >/dev/null 2>&1; do sleep 1; done;
      mc mb -p d/$b >/dev/null; mc mirror -q /backup/$b d/$b >/dev/null; mc ls -r d/$b" | awk '{print $NF}' | sort)"  # 이미지에 awk 없음
  missing="$(comm -23 "$LAST/minio-$b.txt" <(echo "$got") | wc -l | tr -d ' ')"
  echo "  MinIO $b: 백업 시점 $(wc -l < "$LAST/minio-$b.txt" | tr -d ' ')개 중 복원 후 없음 $missing"
  [ "$missing" = 0 ] || fail=1
done
[ "$fail" = 0 ] && echo "복원 훈련 통과" || { echo "복원 훈련 실패" >&2; exit 1; }
