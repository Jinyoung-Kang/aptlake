#!/usr/bin/env bash
# QA 스택에서만 시험용 키를 발급해 qa/.env.keys 에 저장한다 (git 제외 · 권한 600 · 값은 화면에 출력하지 않음).
#   ADMIN    operator(pro)  admin·read·ops        LOADTEST  loadtest   read
#   FREE     demo-free      read                  PRO       demo-pro   read·bulk
#   PRO_B    qa-pro-b(pro)  read·bulk  — 다른 클라이언트 (내보내기·사용량 IDOR 확인용)
#   OPS      qa-ops(free)   ops 만               READ_ONLY_PRO qa-pro-c(pro) read 만 (bulk 없음)
#   WEB      .env.qa 의 WEB_API_KEY (BFF 키, read·ops)
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=qa/.env.keys
cli() { qa/qa.sh exec -T api-internal python -m aptlake_api.cli "$@" </dev/null; }
admin_post() {  # $1 경로 $2 본문 → 응답 JSON
  curl -sf -X POST -H "X-API-Key: $ADMIN" -H 'Content-Type: application/json' -d "$2" "http://127.0.0.1:8711$1"
}
ADMIN=$(cli bootstrap-admin | tail -1)
LOADTEST=$(cli loadtest-key | tail -1)
DEMO=$(cli demo-keys)
FREE=$(echo "$DEMO" | sed -n 's/^free: //p')
PRO=$(echo "$DEMO" | sed -n 's/^pro : //p')
new_key() {  # $1 이름 $2 플랜 $3 스코프 JSON 배열
  cid=$(admin_post /v1/admin/clients "{\"name\":\"$1\",\"planId\":\"$2\"}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("clientId") or d["client_id"])')
  admin_post "/v1/admin/clients/$cid/keys" "{\"scopes\":$3,\"expiresInDays\":30}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("apiKey") or d.get("key"))'
}
PRO_B=$(new_key qa-pro-b pro '["read","bulk"]')
OPS=$(new_key qa-ops free '["ops"]')
READ_ONLY_PRO=$(new_key qa-pro-c pro '["read"]')
WEB=$(sed -n 's/^WEB_API_KEY=//p' qa/.env.qa)
umask 077
{
  for k in ADMIN LOADTEST FREE PRO PRO_B OPS READ_ONLY_PRO WEB; do echo "QA_$k=${!k}"; done
} > "$OUT"
echo "$OUT: 키 $(grep -c '=al_live_' "$OUT")개 저장 (값은 출력하지 않음)"
