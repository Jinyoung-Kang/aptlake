#!/bin/sh
# Lakekeeper 부트스트랩 + 웨어하우스 생성 (멱등: 이미 부트스트랩됐거나 웨어하우스가 있으면 건너뜀).
# 웨어하우스의 저장 자격증명은 catalog 서비스 계정 하나뿐이며, 엔진에는 STS 단기 자격증명이 발급된다.
set -eu
LK=http://lakekeeper:8181

# 연결 실패(000)·5xx 는 카탈로그가 아직 뜨는 중일 수 있어 최대 60초 재시도, 그 밖의 응답은 즉시 판정
i=0
while :; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$LK/management/v1/bootstrap" \
    -H 'Content-Type: application/json' --data '{"accept-terms-of-use": true}') || true
  case "$code" in
    2*|400|409) echo "bootstrap: $code"; break ;;
    000|5*) i=$((i + 1)); [ "$i" -ge 30 ] && { echo "bootstrap failed: $code"; exit 1; }; sleep 2 ;;
    *) echo "bootstrap failed: $code"; exit 1 ;;
  esac
done

if curl -sf "$LK/management/v1/warehouse" | grep -q '"name":"aptlake"'; then
  echo "warehouse: exists"; exit 0
fi

cat > /tmp/wh.json <<JSON
{
  "warehouse-name": "aptlake",
  "project-id": "00000000-0000-0000-0000-000000000000",
  "storage-profile": {
    "type": "s3", "bucket": "lake", "key-prefix": "warehouse",
    "endpoint": "http://minio:9000", "sts-endpoint": "http://minio:9000",
    "region": "us-east-1", "path-style-access": true,
    "flavor": "s3-compat", "sts-enabled": true
  },
  "storage-credential": {
    "type": "s3", "credential-type": "access-key",
    "access-key-id": "catalog", "secret-access-key": "${MINIO_CATALOG_SECRET}"
  }
}
JSON
code=$(curl -s -o /tmp/out -w '%{http_code}' -X POST "$LK/management/v1/warehouse" \
  -H 'Content-Type: application/json' --data @/tmp/wh.json)
case "$code" in 2*) echo "warehouse: $code" ;; *) echo "warehouse failed: $code"; cat /tmp/out; exit 1 ;; esac
