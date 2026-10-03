"""같은 요청 몰림 점검 (QA-009 · 개선 제안 A2): 캐시를 비우고 같은 요청 N건을 동시에 보내 ClickHouse 질의 수를 센다.

  cd api && uv run python ../qa/probes/stampede.py [--n 50] [--path /v1/regions/11110/distribution?ym=2025-06]

QA 스택 전용 (키는 qa/.env.keys, ClickHouse 관리 비밀번호는 qa/.env.qa — 값은 출력하지 않는다).
질의 수는 그 시간 동안 api_reader 의 질의를 모두 센다 — 다른 요청(부하 측정 등)과 겹치지 않게 돌린다.
워커가 여러 개면 워커마다 따로 합치므로 '질의 수 ≤ 워커 수 × 요청 1건의 질의 수'가 기대값이다.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import time
from pathlib import Path

import clickhouse_connect
import httpx

ROOT = Path(__file__).resolve().parents[2]


def env(path: str) -> dict[str, str]:
    pairs = (x.split("=", 1) for x in (ROOT / path).read_text().splitlines() if "=" in x and not x.startswith("#"))
    return {k: v.strip().strip("'\"") for k, v in pairs}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--path", default="/v1/regions/11110/distribution?ym=2025-06")
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    a = ap.parse_args()
    keys, qa = env("qa/.env.keys"), env("qa/.env.qa")
    ch = clickhouse_connect.get_client(
        host="127.0.0.1", port=8740, username="admin", password=qa["CLICKHOUSE_ADMIN_PASSWORD"]
    )
    flush = (
        'redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning --scan --pattern "al:cache:*" '
        '| xargs -r redis-cli --user api --pass "$REDIS_API_PASSWORD" --no-auth-warning del'
    )
    cmd = [str(ROOT / "qa/qa.sh"), "exec", "-T", "redis", "sh", "-c", flush]
    await asyncio.to_thread(subprocess.run, cmd, check=True, capture_output=True)
    start = time.time()
    async with httpx.AsyncClient(base_url=a.base, timeout=30, headers={"X-API-Key": keys["QA_LOADTEST"]}) as c:
        t = time.perf_counter()
        rs = await asyncio.gather(*[c.get(a.path) for _ in range(a.n)])
        ms = (time.perf_counter() - t) * 1000
    ch.command("SYSTEM FLUSH LOGS")
    q = ch.query(
        "SELECT count() FROM system.query_log WHERE type = 'QueryFinish' AND user = 'api_reader' "
        "AND query_start_time_microseconds >= fromUnixTimestamp64Micro({s:Int64})",
        parameters={"s": int(start * 1e6)},
    ).result_rows[0][0]
    caches: dict[str, int] = {}
    for r in rs:
        caches[r.headers.get("X-Cache", "-")] = caches.get(r.headers.get("X-Cache", "-"), 0) + 1
    print(
        f"{a.path} × {a.n}: 상태 {sorted({r.status_code for r in rs})} · 본문 {len({r.content for r in rs})}종 · "
        f"X-Cache {caches} · ClickHouse 질의 {q}회 · 전체 {ms:.0f} ms"
    )


if __name__ == "__main__":
    asyncio.run(main())
