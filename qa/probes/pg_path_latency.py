"""PostgreSQL 을 많이 쓰는 API 경로의 지연 — psycopg 구현(binary · C · 순수 파이썬) 비교용 (QA-005).

  cd api && uv run python ../qa/probes/pg_path_latency.py <표시 이름> [--base http://127.0.0.1:8710]

QA 스택 전용 (키는 qa/.env.keys). 결과 캐시를 피하도록 요청마다 매개변수를 바꾼다. 경로마다 120회, p50·p95.
구현을 바꿔 가며 같은 이미지·같은 데이터로 잰다 (API 이미지만 다시 빌드).
"""

from __future__ import annotations

import argparse
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
N = 120
CASES = {
    "quality/partitions (PG 격자 1.7만 행 중 시도·기간)": lambda i: (
        "/v1/quality/partitions",
        {"from": f"{2021 + i % 5}-01", "to": f"{2021 + i % 5}-12", "sido": ["11", "26", "41", "48", "27"][i % 5]},
    ),
    "quality/partitions/{sgg}/{ym} (PG 상세)": lambda i: (
        f"/v1/quality/partitions/11110/{2021 + i % 5}-{1 + i % 12:02d}",
        {},
    ),
    "ops/errors (PG 로그 + ClickHouse)": lambda i: ("/v1/ops/errors", {"hours": str(1 + i % 700)}),
    "me/usage (키 조회 + ClickHouse)": lambda i: ("/v1/me/usage", {"days": str(1 + i % 90)}),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    a = ap.parse_args()
    keys = dict(line.split("=", 1) for line in (ROOT / "qa/.env.keys").read_text().splitlines())
    read, ops = {"X-API-Key": keys["QA_LOADTEST"]}, {"X-API-Key": keys["QA_ADMIN"]}
    with httpx.Client(base_url=a.base, timeout=30) as c:
        for name, case in CASES.items():
            ms = []
            for i in range(N):
                path, params = case(i)
                t = time.perf_counter()
                r = c.get(path, params={**params, "n": str(i)}, headers=ops if path.startswith("/v1/ops") else read)
                ms.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, (path, r.status_code, r.text[:200])
            ms.sort()
            print(f"{a.label:8} {name:48} p50 {statistics.median(ms):6.1f} ms · p95 {ms[int(N * 0.95) - 1]:6.1f} ms")


if __name__ == "__main__":
    main()
