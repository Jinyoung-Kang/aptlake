"""공개 API 매개변수 퍼징 — 경로·질의 매개변수마다 조작값을 하나씩 넣고(나머지는 정상값) 응답을 분류한다.

  cd api && uv run python ../qa/probes/fuzz_api.py [--base http://127.0.0.1:8710] [--out 결과.jsonl]

QA 스택 전용 (키는 qa/.env.keys). 표시(flag)하는 것:
  5xx · DB/스택 내부 문구 노출 · 2초 넘는 응답 · 오류 응답이 RFC 9457 JSON 이 아님 · 조작값이 2xx 로 받아들여짐(검토용)
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
LEAK = re.compile(r"DB::Exception|Code: \d+|Traceback|psycopg|clickhouse_connect|Syntax error|SQLSTATE|File \"/|"
                  r"asyncpg|sqlalchemy|redis\.exceptions|at line \d+", re.I)  # fmt: skip
PAYLOADS = [
    "", " ", "'", '"', "' OR '1'='1", "1; DROP TABLE trade_current", "%", "_", "\\", "%00", "\x00", "a" * 5000,
    "가", "😀", "../../etc/passwd", "${jndi:ldap://x/a}", "<script>alert(1)</script>", "{{7*7}}", "\r\nX-Injected: 1",
    "NaN", "Infinity", "-Infinity", "-1", "0", "1e309", "99999999999999999999", "-99999999999999999999", "0x10",
    "1.5", "true", "null", "[]", "{}",
    "１１１１０", "١١١١٠", "11110\n", " 11110", "2025-13", "2025-00", "0000-01", "9999-12", "2025-1", "202506",
    "2025-02-30", "2025-06-31T00:00:00", "1900-01-01", "9999-12-31",
]  # fmt: skip


def keys() -> dict[str, str]:
    out = {}
    for line in (ROOT / "qa/.env.keys").read_text().splitlines():
        k, v = line.split("=", 1)
        out[k.removeprefix("QA_")] = v
    return out


def routes(sample: dict[str, str]) -> list[tuple[str, str, dict[str, str], dict[str, str], str]]:
    """(이름, 경로 틀, 경로 값, 질의 값, 키 종류)."""
    s, ck, tid = "11110", sample["complex"], sample["trade"]
    return [
        ("complex", "/v1/complexes/{complexKey}", {"complexKey": ck}, {}, "LOADTEST"),
        ("index", "/v1/index", {}, {"regionId": "00", "method": "HEDONIC_TD_v1"}, "LOADTEST"),
        ("overview", "/v1/market/overview", {}, {"ym": "2025-06"}, "LOADTEST"),
        ("me_usage", "/v1/me/usage", {}, {"days": "7"}, "LOADTEST"),
        ("ops_errors", "/v1/ops/errors", {}, {"hours": "24", "source": "all", "includeResolved": "false",
                                              "includeCleared": "false"}, "ADMIN"),  # fmt: skip
        ("q_partitions", "/v1/quality/partitions", {}, {"from": "2025-01", "to": "2025-06", "sido": "11"}, "LOADTEST"),
        ("q_partition", "/v1/quality/partitions/{sggCd}/{dealYm}", {"sggCd": s, "dealYm": "2025-06"}, {}, "LOADTEST"),
        ("q_rollup", "/v1/quality/rollup", {}, {"from": "2025-01", "to": "2025-06"}, "LOADTEST"),
        ("complexes", "/v1/regions/{sggCd}/complexes", {"sggCd": s}, {"from": "2025-01", "to": "2025-12", "limit": "30"},
         "LOADTEST"),  # fmt: skip
        ("distribution", "/v1/regions/{sggCd}/distribution", {"sggCd": s}, {"ym": "2025-06"}, "LOADTEST"),
        ("months", "/v1/regions/{sggCd}/months", {"sggCd": s}, {"from": "2025-01", "to": "2025-12"}, "LOADTEST"),
        ("search", "/v1/search", {}, {"q": "QA1"}, "LOADTEST"),
        ("trades", "/v1/trades", {}, {"sggCd": s, "from": "2025-06-01", "to": "2025-06-30", "minArea": "30",
                                      "maxArea": "200", "includeCancelled": "true", "limit": "50"}, "LOADTEST"),  # fmt: skip
        ("history", "/v1/trades/{tradeId}/history", {"tradeId": tid}, {}, "LOADTEST"),
    ]


def call(client: httpx.Client, base: str, key: str, name: str, tmpl: str, path: dict, query: dict, where: str, val):
    p = {k: urllib.parse.quote(v, safe="") for k, v in path.items()}
    url = base + tmpl.format(**p)
    t = time.perf_counter()
    try:
        r = client.get(url, params=query, headers={"X-API-Key": key})
        ms = (time.perf_counter() - t) * 1000
        body = r.text[:4000]
        flags = []
        if r.status_code >= 500:
            flags.append("5xx")
        if LEAK.search(body):
            flags.append("leak")
        if ms > 2000:
            flags.append("slow")
        if r.status_code >= 400 and not r.headers.get("content-type", "").startswith("application/problem+json"):
            flags.append("not-problem-json")
        return {"route": name, "param": where, "payload": val[:60] if isinstance(val, str) else val,
                "status": r.status_code, "ms": round(ms, 1), "flags": flags,
                "code": (r.json().get("code") if r.status_code >= 400 and "json" in r.headers.get("content-type", "") else None),
                "excerpt": body[:200] if flags else ""}  # fmt: skip
    except Exception as e:  # 연결 자체가 끊기면 그것도 결함 후보
        return {"route": name, "param": where, "payload": str(val)[:60], "status": 0, "ms": 0, "flags": ["error"],
                "excerpt": repr(e)[:200]}  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8710")
    ap.add_argument("--out", default=str(ROOT / "docs/qa/evidence/qa-fuzz-api.jsonl"))
    ap.add_argument("--sample-complex", required=True)
    ap.add_argument("--sample-trade", required=True)
    a = ap.parse_args()
    k = keys()
    jobs = []
    for name, tmpl, path, query, kind in routes({"complex": a.sample_complex, "trade": a.sample_trade}):
        jobs.append((name, tmpl, path, query, "-", "(정상값)", kind))
        for pk in path:
            for v in PAYLOADS:
                if "/" in v or v in ("", ".", ".."):
                    continue  # 경로 구분자는 경로 매개변수에 넣으면 다른 경로가 된다 (별도 점검)
                jobs.append((name, tmpl, {**path, pk: v}, query, f"path:{pk}", v, kind))
        for qk in query:
            for v in PAYLOADS:
                jobs.append((name, tmpl, path, {**query, qk: v}, f"query:{qk}", v, kind))
        jobs.append((name, tmpl, path, {**query, "unknownParam": "1"}, "query:unknownParam", "1", kind))
    with httpx.Client(timeout=30) as client, ThreadPoolExecutor(6) as pool:
        res = list(pool.map(lambda j: call(client, a.base, k[j[6]], j[0], j[1], j[2], j[3], j[4], j[5]), jobs))
    Path(a.out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in res) + "\n")
    by = {}
    for r in res:
        for f in r["flags"]:
            by.setdefault(f, []).append(r)
    print(f"요청 {len(res)}개 · 상태 코드 분포:", dict(sorted({s: sum(1 for r in res if r['status'] == s) for s in {r['status'] for r in res}}.items())))
    for f, rs in by.items():
        print(f"[{f}] {len(rs)}건")
        for r in rs[:12]:
            print("   ", r["route"], r["param"], repr(r["payload"])[:40], r["status"], r["ms"], r["excerpt"][:120])


if __name__ == "__main__":
    main()
