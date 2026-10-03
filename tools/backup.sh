#!/usr/bin/env bash
# 백업 (make backup): PostgreSQL 3개 DB 덤프 + MinIO 원본(raw)·레이크(lake) 버킷 증분 복사 + .env 사본.
#
#   backups/pg/<시각>/{aptlake,lakekeeper,dagster}.dump, env   ← 실행마다 새 폴더 (작아서 매번 전체)
#   backups/minio/{raw,lake}/                                   ← 증분 복사. 원본에서 지워진 파일도 백업에서는 지우지 않는다
#                                                                  (원본이 실수로 지워졌을 때 다음 백업이 사본까지 지우지 않도록)
# 서빙 DB(ClickHouse)는 레이크에서 다시 발행할 수 있어 백업하지 않는다. Redis 는 캐시·한도 카운터뿐.
# 순서가 중요하다: 카탈로그(lakekeeper DB)를 먼저 덤프하고 레이크 파일을 나중에 복사해야, 복원한 카탈로그가
# 가리키는 메타데이터 파일이 백업에 반드시 들어 있다.
# 이 폴더에는 비밀값(.env)과 키 해시가 들어간다 — 권한 700, git 제외, 클라우드 동기화 폴더에 두지 말 것.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-backups}"
TS="$(date +%Y%m%d-%H%M%S)"
umask 077

running="$(docker compose exec -T postgres psql -U postgres -d dagster -Atc \
  "SELECT count(*) FROM runs WHERE status IN ('STARTED','STARTING','CANCELING')" 2>/dev/null || echo 0)"
if [ "${running:-0}" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "파이프라인 실행 중($running) — 끝난 뒤 다시 실행하세요 (무시하려면 FORCE=1). 실행 중 백업은 카탈로그·파일이 어긋날 수 있습니다." >&2
  exit 1
fi

mkdir -p "$DEST/pg/$TS" "$DEST/minio"
chmod 700 "$DEST"
for db in aptlake lakekeeper dagster; do
  docker compose exec -T postgres pg_dump -U postgres -Fc "$db" > "$DEST/pg/$TS/$db.dump"
done
cp .env "$DEST/pg/$TS/env"
echo "postgres: $(du -sh "$DEST/pg/$TS" | cut -f1) → $DEST/pg/$TS"

docker compose run --rm --no-deps -T -v "$PWD/$DEST/minio:/backup" --entrypoint sh minio-init -c '
  set -e
  mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
  for b in raw lake; do mc mirror --quiet --overwrite --preserve "local/$b" "/backup/$b"; done
' >/dev/null
echo "minio: $(du -sh "$DEST/minio" | cut -f1) → $DEST/minio (증분)"
echo "확인: make backup-verify"
