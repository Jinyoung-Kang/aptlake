#!/bin/sh
# 버킷·서비스 계정·최소 권한 정책 (기획서 9장 '저장소').
#   raw     : 원본 XML 불변 보관 — Object Lock(GOVERNANCE) + ingest 는 PutObject/GetObject 만
#   lake    : Iceberg 데이터·메타데이터 — catalog(Lakekeeper) 계정만 접근, 엔진(Trino·PyIceberg)은
#             Lakekeeper 가 STS 로 발급한 테이블 경로 범위 단기 자격증명으로만 접근
#   exports : Parquet 내보내기 — export 계정만, 1일 뒤 자동 삭제
set -eu

# 의존 서비스가 막 뜬 직후(데몬 재시작 등)에도 실패하지 않도록 최대 60초 기다린다
i=0
until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 \
  && mc ready local >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -ge 30 ] && { echo "minio not ready"; exit 1; }; sleep 2
done

mc mb --ignore-existing --with-lock local/raw
mc retention set --default GOVERNANCE 365d local/raw >/dev/null
mc mb --ignore-existing local/lake
mc mb --ignore-existing local/exports
# 수명주기 설정은 통째로 교체 (재실행해도 규칙이 중복되지 않음)
echo '{"Rules":[{"ID":"expire-exports","Status":"Enabled","Expiration":{"Days":1},"Filter":{"Prefix":""}}]}' \
  | mc ilm import local/exports >/dev/null

cat > /tmp/ingest.json <<'JSON'
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],"Resource":["arn:aws:s3:::raw/*"]},
 {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":["arn:aws:s3:::raw"]}]}
JSON
cat > /tmp/catalog.json <<'JSON'
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:*"],"Resource":["arn:aws:s3:::lake","arn:aws:s3:::lake/*"]}]}
JSON
cat > /tmp/export.json <<'JSON'
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],"Resource":["arn:aws:s3:::exports/*"]}]}
JSON

for svc in ingest catalog export; do
  mc admin policy create local "aptlake-$svc" "/tmp/$svc.json" >/dev/null
done

mc admin user add local ingest  "$MINIO_INGEST_SECRET"  >/dev/null
mc admin user add local catalog "$MINIO_CATALOG_SECRET" >/dev/null
mc admin user add local export  "$MINIO_EXPORT_SECRET"  >/dev/null
mc admin policy attach local aptlake-ingest  --user ingest  >/dev/null 2>&1 || true
mc admin policy attach local aptlake-catalog --user catalog >/dev/null 2>&1 || true
mc admin policy attach local aptlake-export  --user export  >/dev/null 2>&1 || true
echo "minio init done"
