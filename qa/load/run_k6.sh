#!/usr/bin/env bash
# QA 스택 부하 측정 1회: QA Redis 결과 캐시 비움 → k6 (QA 네트워크 안에서 api:8610 직접) → 요약 + 서버측 질의 비용.
#   qa/load/run_k6.sh <이름> api.js|mixed.js [ENV=값 ...]   예: qa/load/run_k6.sh t300 api.js RATE_MONTHS=200 RATE_TRADES=100
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME="$1"; SCRIPT="$2"; shift 2
OUT="${QA_LOAD_OUT:-docs/qa/evidence/load}"; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)  # 절대 경로도 받는다 (k6 볼륨 마운트)
KEY="$(sed -n 's/^QA_LOADTEST=//p' qa/.env.keys)"
qa/qa.sh exec -T redis sh -c 'redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning --scan --pattern "al:cache:*" | xargs -r redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning del' >/dev/null
SRC=loadtest; [ "$SCRIPT" = mixed.js ] && SRC=qa/load
START=$(date -u +%s)
envs=(-e APTLAKE_KEY="$KEY" -e BASE=http://api:8610 -e DURATION="${DURATION:-60s}")
for kv in "$@"; do envs+=(-e "$kv"); done
docker run --rm --network aptlake-qa_default "${envs[@]}" -v "$PWD/$SRC:/scripts:ro" -v "$OUT:/out" \
  grafana/k6:1.3.0 run --quiet --summary-export="/out/$NAME.json" "/scripts/$SCRIPT" >/dev/null 2>&1 || true
python3 - "$OUT/$NAME.json" "$NAME" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))["metrics"]
trends = sorted(k for k, v in m.items() if k.endswith("_ms") and "p(95)" in v)
parts = [f"{k[:-3]} p50 {m[k]['med']:.0f} · p95 {m[k]['p(95)']:.0f}" for k in trends]
print(f"{sys.argv[2]:10} 처리량 {m['http_reqs']['rate']:.1f} req/s · 실패 {m['http_req_failed']['value']:.4f} · 시작 못한 반복 {m.get('dropped_iterations', {}).get('count', 0)} | " + " | ".join(parts))
PY
qa/qa.sh exec -T clickhouse sh -c "clickhouse-client --user \"\$CLICKHOUSE_USER\" --password \"\$CLICKHOUSE_PASSWORD\" -q \"SYSTEM FLUSH LOGS; SELECT 'CH', count() q, round(quantile(0.95)(query_duration_ms)) p95_ms, round(sum(ProfileEvents['OSCPUVirtualTimeMicroseconds'])/1e6,1) cpu_s FROM system.query_log WHERE type='QueryFinish' AND user='api_reader' AND event_time >= toDateTime($START) FORMAT TSV\""
