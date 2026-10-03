"""장애 주입: 부하를 흘리면서 QA 스택의 의존 구성요소를 잠시 멈췄다(docker pause) 풀고, 구간별 응답을 비교한다.

  cd api && uv run python ../qa/probes/chaos.py clickhouse|redis|postgres [--pause 15]

QA 스택 컨테이너(aptlake-qa-*)만 멈춘다. 결과 캐시를 피하려고 매 요청의 기간을 바꾼다.
구간: 전(8초) · 멈춤(--pause 초) · 푼 뒤(15초). 구간마다 경로별 상태 코드 분포와 지연 p50/p95/최대.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
BASE = __import__("os").environ.get("QA_BASE", "http://127.0.0.1:8710")


def keys() -> dict[str, str]:
    return {
        k.removeprefix("QA_"): v for k, v in (l.split("=", 1) for l in (ROOT / "qa/.env.keys").read_text().splitlines())
    }


def request(rng: random.Random) -> tuple[str, str, dict]:
    y, m = rng.randint(2021, 2025), rng.randint(1, 12)
    sgg = rng.choice(["11110", "26110", "41130", "48110", "30110", "11130", "27110"])
    kind = rng.choice(["months", "trades", "ops", "regions"])
    if kind == "months":
        return kind, f"/v1/regions/{sgg}/months", {"from": f"{y}-{m:02d}", "to": f"{y + 1}-{m:02d}"}
    if kind == "trades":
        return kind, "/v1/trades", {"sggCd": sgg, "from": f"{y}-{m:02d}-01", "to": f"{y}-{m:02d}-28", "limit": "50"}
    if kind == "ops":
        return kind, "/v1/ops/errors", {"hours": str(rng.randint(1, 720))}
    return kind, f"/v1/regions/{sgg}/distribution", {"ym": f"{y}-{m:02d}"}


async def load(client: httpx.AsyncClient, k: dict, stop: asyncio.Event, out: list, phase: list[str], rps: int):
    rng = random.Random(7)
    while not stop.is_set():
        kind, path, params = request(rng)
        key = k["ADMIN"] if kind == "ops" else k["LOADTEST"]

        async def one(kind=kind, path=path, params=params, key=key, ph=phase[0]):
            t = time.perf_counter()
            try:
                r = await client.get(BASE + path, params=params, headers={"X-API-Key": key})
                code = r.status_code
            except httpx.HTTPError as e:
                code = type(e).__name__
            out.append((ph, kind, code, (time.perf_counter() - t) * 1000))

        asyncio.create_task(one())
        await asyncio.sleep(1 / rps)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", choices=["clickhouse", "redis", "postgres", "api"])
    ap.add_argument("--restart", action="store_true", help="멈춤 대신 docker restart (진행 중 요청·재시작 동안 요청)")
    ap.add_argument("--pause", type=int, default=15)
    ap.add_argument("--rps", type=int, default=20)
    a = ap.parse_args()
    container = f"aptlake-qa-{a.target}-1"
    k, out, phase, stop = keys(), [], ["전"], asyncio.Event()
    async with httpx.AsyncClient(timeout=30) as client:
        task = asyncio.create_task(load(client, k, stop, out, phase, a.rps))
        await asyncio.sleep(8)
        phase[0] = "멈춤"
        if a.restart:
            await asyncio.to_thread(subprocess.run, ["docker", "restart", "-t", "10", container], check=True)
        else:
            subprocess.run(["docker", "pause", container], check=True)
            try:
                await asyncio.sleep(a.pause)
            finally:
                subprocess.run(["docker", "unpause", container], check=True)
        phase[0] = "푼 뒤"
        await asyncio.sleep(15)
        stop.set()
        await task
        await asyncio.sleep(31)  # 마지막 요청들이 끝나거나 시간 초과될 때까지
    print(f"== {a.target} {'재시작' if a.restart else f'{a.pause}초 멈춤'} (요청 {len(out)}건, {a.rps} rps)")
    for ph in ("전", "멈춤", "푼 뒤"):
        by = defaultdict(list)
        for p, kind, code, ms in out:
            if p == ph:
                by[kind].append((code, ms))
        for kind, rs in sorted(by.items()):
            ms = sorted(m for _, m in rs)
            p95 = ms[int(len(ms) * 0.95) - 1] if len(ms) >= 20 else max(ms)
            print(
                f"  {ph:4} {kind:8} {dict(Counter(c for c, _ in rs))}  p50 {statistics.median(ms):.0f}ms p95 {p95:.0f}ms max {max(ms):.0f}ms"
            )


if __name__ == "__main__":
    asyncio.run(main())
