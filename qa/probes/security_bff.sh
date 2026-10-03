#!/usr/bin/env bash
# 웹 BFF 권한 점검 (QA 스택 웹 3710): 키 없는 요청이 Sec-Fetch-Site: same-origin 헤더(브라우저 밖에서 위조 가능)로
# 웹 키를 얻었을 때 할 수 있는 일. 결정(QA-001): 수집 상태 보기는 공개, 로그 비우기·되돌리기는 운영자 키만 → 403 이어야 한다.
set -uo pipefail
B="${1:-http://127.0.0.1:3710}"; fail=0
for p in /v1/ops/status /v1/ops/errors /v1/ops/connectivity; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -H 'Sec-Fetch-Site: same-origin' "$B$p"); echo "GET $p → $c (기대 200: 보기는 공개)"; [ "$c" = 200 ] || fail=1
done
for m in POST DELETE; do
  c=$(curl -s -o /dev/null -w '%{http_code}' -X $m -H 'Sec-Fetch-Site: same-origin' "$B/v1/ops/errors/clear"); echo "$m /v1/ops/errors/clear → $c (기대 403: 운영자 키 필요)"; [ "$c" = 403 ] || fail=1
done
c=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Sec-Fetch-Site: cross-site' -H 'Origin: https://evil.example' "$B/v1/ops/errors/clear"); echo "교차 출처 POST → $c (기대 403)"; [ "$c" = 403 ] || fail=1
exit $fail
