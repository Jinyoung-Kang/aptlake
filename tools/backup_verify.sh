#!/usr/bin/env bash
# 백업 복원 확인 (make backup-verify): 가장 최근의 '완료된' 백업을 임시 PostgreSQL 컨테이너에 실제로 복원하고,
# 백업할 때 적어 둔 값(manifest.txt)과 비교한다 — 운영 값과 비교하면 백업 뒤 정상적인 변화(발행·격자 등록)에도 실패해
# 경고를 무시하게 된다. MinIO 는 백업 시점에 있던 객체가 사본에 모두 있는지 이름으로 본다. 운영 데이터는 건드리지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${BACKUP_DIR:-backups}"
LAST="$(ls -1d "$DEST"/pg/*/ 2>/dev/null | grep -v '\.partial/$' | sort | tail -1 || true)"
[ -n "$LAST" ] || { echo "완료된 백업이 없습니다: make backup" >&2; exit 1; }
[ -f "$LAST/manifest.txt" ] || { echo "$LAST 에 manifest.txt 가 없습니다 (이전 형식이거나 미완료) — make backup 으로 새로 만드세요" >&2; exit 1; }
case "$LAST" in /*) ABS="$LAST" ;; *) ABS="$PWD/$LAST" ;; esac
NAME="aptlake-restore-check-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=restorecheck -v "$ABS:/dump:ro" postgres:16-alpine >/dev/null
until docker exec "$NAME" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done
sleep 2
for db in aptlake lakekeeper dagster; do
  docker exec "$NAME" createdb -U postgres "$db"
  docker exec "$NAME" pg_restore -U postgres -d "$db" --no-owner --no-privileges --exit-on-error "/dump/$db.dump"
done

fail=0
echo "복원본 ↔ 백업 시점 값 ($LAST):"
while IFS='|' read -r db sql want; do
  case "$db" in
    clickhouse)  # 내보낸 사용량 파일이 끝까지 읽히고 행 수가 같은가
      got="$(docker compose exec -T clickhouse clickhouse local --input-format Native \
        --query "SELECT count() FROM table" < "$LAST/usage_event.native")" ;;
    *) got="$(docker exec "$NAME" psql -U postgres -d "$db" -Atc "$sql")" ;;
  esac
  if [ "$got" = "$want" ]; then echo "  OK   $db: $sql = $got"; else echo "  DIFF $db: $sql 복원 $got / 백업 시점 $want"; fail=1; fi
done < "$LAST/manifest.txt"

for b in raw lake; do  # 백업 시점에 있던 객체 ⊆ 사본
  have="$(mktemp)"
  (cd "$DEST/minio/$b" && find . -type f | sed 's|^\./||' | sort) > "$have"
  missing="$(comm -23 "$LAST/minio-$b.txt" "$have" | wc -l | tr -d ' ')"
  total="$(wc -l < "$LAST/minio-$b.txt" | tr -d ' ')"
  rm -f "$have"
  if [ "$total" -gt 0 ] && [ "$missing" = 0 ]; then echo "  OK   minio $b: 백업 시점 객체 $total 개 모두 사본에 있음"
  else echo "  DIFF minio $b: 백업 시점 객체 $total 개 중 사본에 없음 $missing"; fail=1; fi
done
[ "$fail" = 0 ] && echo "백업 복원 확인 통과" || { echo "백업 복원 확인 실패" >&2; exit 1; }
