#!/usr/bin/env bash
# 백업 (make backup): PostgreSQL 3개 DB 덤프 + ClickHouse 사용량 이벤트 + MinIO 원본(raw)·레이크(lake) 버킷 증분 복사 + .env 사본.
#
#   backups/pg/<시각>/{aptlake,lakekeeper,dagster}.dump, env   ← 실행마다 새 폴더 (작아서 매번 전체)
#                    /usage_event.native                       ← API 사용량·5xx 기록 (레이크에 없어 다시 만들 수 없다)
#                    /manifest.txt, minio-{raw,lake}.txt       ← 백업 시점의 핵심 행 수·버킷 객체 목록 (backup-verify 가 이것과 비교)
#   backups/minio/{raw,lake}/                                   ← 증분 복사. 원본에서 지워진 파일도 백업에서는 지우지 않는다
#                                                                  (원본이 실수로 지워졌을 때 다음 백업이 사본까지 지우지 않도록)
# 서빙 표(거래·통계·지수)는 레이크에서 다시 발행할 수 있어 백업하지 않는다. Redis 는 캐시·한도 카운터뿐.
# 순서가 중요하다: 카탈로그(lakekeeper DB)를 먼저 덤프하고 레이크 파일을 나중에 복사해야, 복원한 카탈로그가
# 가리키는 메타데이터 파일이 백업에 반드시 들어 있다.
# 모든 단계가 끝나야 <시각>.partial 폴더를 <시각> 으로 바꾼다 — 중간에 실패한 백업이 '가장 최근 백업'으로 보이지 않게.
# 이 폴더에는 비밀값(.env)과 키 해시가 들어간다 — 권한 700, git 제외, 클라우드 동기화 폴더에 두지 말 것.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-backups}"
case "$DEST" in /*) ABS="$DEST" ;; *) ABS="$PWD/$DEST" ;; esac   # 도커 볼륨은 절대 경로여야 한다
TS="$(date +%Y%m%d-%H%M%S)"
WORK="$DEST/pg/$TS.partial"
umask 077

# 실행 중·대기 중인 작업이 있으면 거부 — 확인 질의가 실패해도 거부한다(모르면 멈춘다)
if ! running="$(docker compose exec -T postgres psql -U postgres -d dagster -Atc \
  "SELECT count(*) FROM runs WHERE status IN ('QUEUED','NOT_STARTED','STARTING','STARTED','CANCELING')")"; then
  running="확인 실패"
fi
if [ "$running" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "파이프라인 실행·대기 중($running) — 끝난 뒤 다시 실행하세요 (무시하려면 FORCE=1). 실행 중 백업은 카탈로그·파일이 어긋날 수 있습니다." >&2
  exit 1
fi

mkdir -p "$WORK" "$DEST/minio"
chmod 700 "$DEST"
q() { docker compose exec -T postgres psql -U postgres -d "$1" -Atc "$2"; }
for db in aptlake lakekeeper dagster; do
  docker compose exec -T postgres pg_dump -U postgres -Fc "$db" > "$WORK/$db.dump"
done
cp .env "$WORK/env"
# 덤프 직후의 값 — 복원본이 이 값과 같아야 한다 (운영 값은 백업 뒤에도 바뀌므로 비교 기준으로 쓰지 않는다)
{
  echo "aptlake|SELECT count(*) FROM api.client|$(q aptlake 'SELECT count(*) FROM api.client')"
  echo "aptlake|SELECT count(*) FROM api.api_key|$(q aptlake 'SELECT count(*) FROM api.api_key')"
  echo "aptlake|SELECT count(*) FROM ops.ingest_partition|$(q aptlake 'SELECT count(*) FROM ops.ingest_partition')"
  echo "aptlake|SELECT version FROM ops.dataset_version ORDER BY published_at DESC LIMIT 1|$(q aptlake 'SELECT version FROM ops.dataset_version ORDER BY published_at DESC LIMIT 1')"
  echo "lakekeeper|SELECT count(*) FROM tabular|$(q lakekeeper 'SELECT count(*) FROM tabular')"
} > "$WORK/manifest.txt"
echo "postgres: $(du -sh "$WORK" | cut -f1)"

# API 사용량 이벤트 (TTL 180일) — 컨테이너 안의 관리자 계정으로 (비밀값을 밖으로 꺼내지 않음)
docker compose exec -T clickhouse sh -c \
  'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -q "SELECT * FROM aptlake.usage_event FORMAT Native"' \
  > "$WORK/usage_event.native"
# 내보낸 파일 자체의 행 수 (실시간 표는 그 사이에도 늘어나므로 파일을 다시 읽어 센다)
echo "clickhouse|usage_event.native rows|$(docker compose exec -T clickhouse \
  clickhouse local --input-format Native --query "SELECT count() FROM table" < "$WORK/usage_event.native")" >> "$WORK/manifest.txt"

# 버킷 복사 + 복사 직전의 객체 목록(이름) — 확인 단계가 '그때 있던 객체가 모두 사본에 있는지'를 본다
docker compose run --rm --no-deps -T -v "$ABS/minio:/backup" -v "$ABS/pg/$TS.partial:/work" --entrypoint sh minio-init -c '
  set -e
  mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  for b in raw lake; do
    mc ls --recursive "local/$b" > "/work/minio-$b.raw"
    mc mirror --quiet --overwrite --preserve "local/$b" "/backup/$b"
  done
' >/dev/null
for b in raw lake; do  # `[날짜 시각 시간대] 크기 클래스 키` → 키
  awk '{print $NF}' "$WORK/minio-$b.raw" | sort > "$WORK/minio-$b.txt"
  rm "$WORK/minio-$b.raw"
done
mv "$WORK" "$DEST/pg/$TS"
echo "minio: $(du -sh "$DEST/minio" | cut -f1) → $DEST/minio (증분)"
echo "완료: $DEST/pg/$TS — 확인: make backup-verify"
