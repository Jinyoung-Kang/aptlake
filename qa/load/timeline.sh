#!/usr/bin/env bash
# QA 스택 부하 1회를 초 단위로 본다 (QA-009): 결과 캐시 비움 → k6(api.js) 60초 → 초마다 지연 p95 와 CPU(VM 전체·dockerd·컨테이너).
#   qa/load/timeline.sh <이름> [ENV=값 ...]    예: qa/load/timeline.sh t300 RATE_MONTHS=200 RATE_TRADES=100
# 출력: 요약(전체 / 첫 3초와 dockerd 급증 초·그다음 초를 뺀 값 / 30초 이후) + ClickHouse 질의 수.
# 원자료(k6 CSV, 회당 약 1MB)는 QA_LOAD_OUT(기본: 저장소 밖 임시 폴더)에 남긴다.
# CPU 는 도커 VM 의 cgroup 사용량(usage_usec)을 1초마다 읽는다 — docker stats 보다 측정 부담이 작다.
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME="$1"; shift
OUT="${QA_LOAD_OUT:-${TMPDIR:-/tmp}/aptlake-qa-timeline}"; mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
KEY="$(sed -n 's/^QA_LOADTEST=//p' qa/.env.keys)"
docker rm -f qa-timeline-k6 qa-timeline-cg >/dev/null 2>&1 || true
docker run -d --name qa-timeline-cg --cgroupns host -v /sys/fs/cgroup:/cg:ro alpine:3.22 sh -c '
  i=0; while [ $i -lt 78 ]; do t=$(date +%s)
    for d in /cg /cg/restricted /cg/docker/*; do echo "C $t ${d##*/} $(sed -n "s/^usage_usec //p" $d/cpu.stat)"; done
    i=$((i+1)); sleep 1; done' >/dev/null
sleep 3
qa/qa.sh exec -T redis sh -c 'redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning --scan --pattern "al:cache:*" | xargs -r redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning del' >/dev/null
envs=(-e APTLAKE_KEY="$KEY" -e BASE=http://api:8610 -e DURATION=60s)
for kv in "$@"; do envs+=(-e "$kv"); done
docker run -d --name qa-timeline-k6 --network aptlake-qa_default "${envs[@]}" -v "$PWD/loadtest:/scripts:ro" -v "$OUT:/out" \
  grafana/k6:1.3.0 run --quiet --out "csv=/out/$NAME.csv" /scripts/api.js >/dev/null
docker ps --no-trunc --format '{{.ID}} {{.Names}}' > "$OUT/$NAME.ids"
docker wait qa-timeline-k6 >/dev/null; docker rm qa-timeline-k6 >/dev/null
docker wait qa-timeline-cg >/dev/null; docker logs qa-timeline-cg > "$OUT/$NAME.cg" 2>/dev/null; docker rm qa-timeline-cg >/dev/null
gzip -f "$OUT/$NAME.csv"
python3 - "$OUT" "$NAME" <<'PY'
import collections, csv, gzip, sys
out, name = sys.argv[1:]
names = dict(line.split() for line in open(f"{out}/{name}.ids"))
cpu = collections.defaultdict(dict)
for line in open(f"{out}/{name}.cg"):
    p = line.split()
    if len(p) == 4 and p[3].isdigit():
        cpu[int(p[1])][p[2]] = int(p[3])
ts = sorted(cpu)
pct = lambda a, b, k: (cpu[b].get(k, 0) - cpu[a].get(k, 0)) / 1e4 / (b - a)
spike = {b for a, b in zip(ts, ts[1:]) if pct(a, b, "restricted") > 25}  # dockerd 가 코어의 25% 넘게 쓴 초
by = {"months": collections.defaultdict(list), "trades": collections.defaultdict(list)}
with gzip.open(f"{out}/{name}.csv.gz", "rt") as f:
    for r in csv.DictReader(f):
        if r["metric_name"] == "http_req_duration" and r.get("scenario") in by:
            by[r["scenario"]][int(r["timestamp"])].append(float(r["metric_value"]))
t0 = min(min(v) for v in by.values())
q = lambda v: (lambda s: f"p50 {s[len(s) // 2]:.0f} · p95 {s[int(len(s) * .95)]:.0f} ms (n {len(s)})")(sorted(v)) if v else "-"
for sc, d in by.items():
    allv = [x for vs in d.values() for x in vs]
    calm = [x for t, vs in d.items() if t not in spike and t - 1 not in spike and t - t0 >= 3 for x in vs]
    late = [x for t, vs in d.items() if t - t0 >= 30 for x in vs]
    print(f"{name} {sc:6} 전체 {q(allv)} | 첫 3초·dockerd 급증 제외 {q(calm)} | 30초 이후 {q(late)}")
a, b = ts[4], ts[-4]
ids = {v: k for k, v in names.items()}
row = [f"VM {pct(a, b, 'cg'):.0f}", f"dockerd {pct(a, b, 'restricted'):.0f}"]
for c in ("clickhouse", "api", "redis"):
    k = ids.get(f"aptlake-qa-{c}-1")
    row.append(f"{c} {pct(a, b, k):.0f}" if k else f"{c} -")
prod = sum(pct(a, b, k) for k, n in names.items() if n.startswith("aptlake-") and not n.startswith("aptlake-qa-"))
print(f"{name} CPU 평균(%, 100=코어 1개): " + " · ".join(row) + f" · 운영 스택 합 {prod:.0f} · dockerd 급증 {len(spike)}초")
PY
START=$(head -1 "$OUT/$NAME.cg" | awk '{print $2}'); END=$(tail -1 "$OUT/$NAME.cg" | awk '{print $2}')
qa/qa.sh exec -T clickhouse sh -c "clickhouse-client --user \"\$CLICKHOUSE_USER\" --password \"\$CLICKHOUSE_PASSWORD\" -q \"SYSTEM FLUSH LOGS; SELECT '$NAME ClickHouse', count(), 'p95_ms', round(quantile(0.95)(query_duration_ms)), 'cpu_s', round(sum(ProfileEvents['OSCPUVirtualTimeMicroseconds'])/1e6,1), 'region_month', countIf(query LIKE '%FROM region_month%') FROM system.query_log WHERE type='QueryFinish' AND user='api_reader' AND event_time BETWEEN toDateTime($START) AND toDateTime($END) FORMAT TSV\""
rm -f "$OUT/$NAME.ids"
