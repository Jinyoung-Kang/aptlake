"""변경 전후 비교용 측정 (실행 중인 로컬 스택 대상). 결과는 docs/performance.md 에 옮겨 적는다.

cd api && uv run python ../tools/bench.py ops        # 운영 API 지연 (웹 BFF 경유, 캐시 없음)
cd api && uv run python ../tools/bench.py geo-cold   # 경계 GeoJSON 첫 요청이 같은 워커의 다른 요청을 막는 정도
                                                     #   (API 컨테이너를 막 재시작한 직후에 실행 — 경계 캐시가 빈 상태)
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import httpx

WEB = "http://127.0.0.1:3610"  # nginx BFF (같은 출처 요청에만 웹 키를 붙인다)
API = "http://127.0.0.1:8610"
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def summary(name: str, ms: list[float]) -> None:
    s = sorted(ms)
    p95 = s[min(len(s) - 1, round(0.95 * (len(s) - 1)))]
    print(f"{name:34s} n={len(s):4d}  med={statistics.median(s):8.1f}  p95={p95:8.1f}  max={s[-1]:8.1f} ms")


async def ops(n: int = 15) -> None:
    async with httpx.AsyncClient(base_url=WEB, headers=SAME_ORIGIN, timeout=120) as c:
        for path in ("/v1/ops/errors?hours=168", "/v1/ops/status"):
            ms = []
            for _ in range(n):
                t = time.perf_counter()
                r = await c.get(path)
                ms.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, (path, r.status_code)
            summary(path, ms)


async def geo_cold() -> None:
    """워커 4개 모두 경계 캐시가 빈 상태에서 경계 4건 + 같은 시각 /healthz 200건(2초에 걸쳐)."""
    async with httpx.AsyncClient(base_url=API, timeout=60, limits=httpx.Limits(max_connections=300)) as c:

        async def timed(path: str, delay: float, headers: dict | None = None) -> float:
            await asyncio.sleep(delay)
            t = time.perf_counter()
            r = await c.get(path, headers=headers)
            assert r.status_code == 200, (path, r.status_code)
            return (time.perf_counter() - t) * 1000

        geo = [timed("/v1/geo/sgg", 0.2, {"Accept-Encoding": "gzip"}) for _ in range(4)]
        health = [timed("/healthz", i * 0.01) for i in range(200)]
        res = await asyncio.gather(*geo, *health)
    summary("geo (cold, 4 workers)", res[:4])
    summary("/healthz during cold geo", res[4:])


if __name__ == "__main__":
    asyncio.run({"ops": ops, "geo-cold": geo_cold}[sys.argv[1]]())
