"""값 대조: QA 스택의 API 응답을 ClickHouse 직접 집계와 맞춰 본다 (API 의 질의 문장을 재사용하지 않고 화면 주석의 정의로).

  cd api && uv run python ../qa/probes/correctness.py [--seed 7]

정의 (API notes 와 같음): 거래 = 해제 제외, 분위수·히스토그램 표본 = 해제·면적 0·이상치 제외(PRICED), 분위수는 정확 분위수(포함 보간).
C1 지역 통계 · C2 거래 목록(커서를 끝까지 넘겨 빠짐·중복·순서, 요약) · C3 분포 · C4 단지 순위 · C5 시장 개요 · C6 발행 뒤 캐시 무효화
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import time
from pathlib import Path

import clickhouse_connect
import httpx
import redis

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8710"
PRICED = "is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0"
FAILS: list[str] = []
CHECKS = [0]


def env(path: str) -> dict[str, str]:
    return dict(l.split("=", 1) for l in (ROOT / path).read_text().splitlines() if "=" in l and not l.startswith("#"))


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS[0] += 1
    if not ok:
        FAILS.append(f"{name}: {detail}")


def r1(v):
    return None if v is None else round(float(v), 1)


def ym_add(y: int, m: int, k: int) -> tuple[int, int]:
    i = y * 12 + m - 1 + k
    return i // 12, i % 12 + 1


def month_end(y: int, m: int) -> dt.date:
    ny, nm = ym_add(y, m, 1)
    return dt.date(ny, nm, 1) - dt.timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    e, k = env("qa/.env.qa"), env("qa/.env.keys")
    ch = clickhouse_connect.get_client(host="127.0.0.1", port=8740, username="admin",
                                       password=e["CLICKHOUSE_ADMIN_PASSWORD"], database="aptlake")  # fmt: skip
    api = httpx.Client(base_url=API, headers={"X-API-Key": k["QA_LOADTEST"]}, timeout=30)
    sggs = [r[0] for r in ch.query("SELECT sgg_cd FROM region ORDER BY sgg_cd").result_rows]
    q = lambda sql, p=None: ch.query(sql, parameters=p or {}).result_rows  # noqa: E731

    # C1 지역 통계 (region_month 를 거래 표에서 다시 계산해 대조 — 집계 표와 거래 표의 일관성까지)
    for _ in range(20):
        s, y, m, span = rng.choice(sggs), rng.randint(2021, 2025), rng.randint(1, 12), rng.randint(1, 24)
        ty, tm = ym_add(y, m, span - 1)
        if (ty, tm) > (2026, 9):
            ty, tm = 2026, 9
        body = api.get(f"/v1/regions/{s}/months", params={"from": f"{y}-{m:02d}", "to": f"{ty}-{tm:02d}"}).json()
        exp = {f"{r[0]:%Y-%m}": r[1:] for r in q(f"""
            SELECT toStartOfMonth(deal_date) AS mo, count(), countIf(is_cancelled = 0), countIf(is_cancelled = 1),
                   countIf({PRICED}), countIf(is_outlier = 1),
                   quantileExactInclusiveIf(0.25)(ppm2, {PRICED}), quantileExactInclusiveIf(0.5)(ppm2, {PRICED}),
                   quantileExactInclusiveIf(0.75)(ppm2, {PRICED})
            FROM trade_current WHERE sgg_cd = %(s)s AND deal_date BETWEEN %(a)s AND %(b)s GROUP BY mo ORDER BY mo""",
            {"s": s, "a": dt.date(y, m, 1), "b": month_end(ty, tm)})}  # fmt: skip
        got = {i["dealYm"]: i for i in body["items"]}
        check("C1 months 달 목록", set(got) == set(exp), f"{s} {y}-{m}~{ty}-{tm}: {sorted(set(got) ^ set(exp))}")
        for mo, (rep, tr, can, pr, out, p25, p50, p75) in exp.items():
            i = got.get(mo)
            if not i:
                continue
            want = (rep, tr, can, pr, out) + ((r1(p25), r1(p50), r1(p75)) if pr >= 5 else (None, None, None))
            have = (i["reported"], i["trades"], i["cancelled"], i["sampleSize"], i["outliers"],
                    i["p25PricePerM2"], i["medianPricePerM2"], i["p75PricePerM2"])  # fmt: skip
            check("C1 months 값", want == have, f"{s} {mo}: 기대 {want} / 응답 {have}")

    # C2 거래 목록: 커서를 끝까지 넘겨 집합·순서·중복, 요약값
    for _ in range(12):
        s, y, m = rng.choice(sggs), rng.randint(2021, 2026), rng.randint(1, 12)
        if (y, m) > (2026, 9):
            y, m = 2026, 9
        a0, b0 = dt.date(y, m, 1), month_end(y, m)
        for inc in (False, True):
            for area in (None, (59.97, 84.98)):
                params = {
                    "sggCd": s,
                    "from": str(a0),
                    "to": str(b0),
                    "limit": "37",
                    "includeCancelled": str(inc).lower(),
                }
                cond = "" if inc else " AND is_cancelled = 0"
                if area:
                    params |= {"minArea": str(area[0]), "maxArea": str(area[1])}
                    cond += f" AND area_m2 >= {area[0]} AND area_m2 <= {area[1]}"
                ids, summary, cur, pages = [], None, None, 0
                while True:
                    body = api.get("/v1/trades", params=params | ({"cursor": cur} if cur else {})).json()
                    summary = summary or body["summary"]
                    ids += [(i["dealDate"], i["tradeId"]) for i in body["items"]]
                    cur, pages = body["page"]["nextCursor"], pages + 1
                    if not cur or pages > 100:
                        break
                exp = [(f"{r[0]}", r[1]) for r in q(f"""SELECT deal_date, trade_id FROM trade_current
                       WHERE sgg_cd = %(s)s AND deal_date BETWEEN %(a)s AND %(b)s{cond}
                       ORDER BY deal_date DESC, trade_id DESC""", {"s": s, "a": a0, "b": b0})]  # fmt: skip
                tag = f"{s} {y}-{m:02d} 해제포함={inc} 면적={area}"
                check("C2 trades 페이지 전체 = 기대 (순서 포함)", ids == exp,
                      f"{tag}: 응답 {len(ids)}건(중복 {len(ids) - len(set(ids))}) / 기대 {len(exp)}건")  # fmt: skip
                (n, can, med, medp), = q(f"""SELECT count(), countIf(is_cancelled = 1),
                       quantileExactInclusiveIf(0.5)(ppm2, {PRICED}), quantileExactInclusiveIf(0.5)(price_manwon, is_cancelled = 0)
                       FROM trade_current WHERE sgg_cd = %(s)s AND deal_date BETWEEN %(a)s AND %(b)s{cond}""",
                       {"s": s, "a": a0, "b": b0})  # fmt: skip
                want = (n, can, r1(med) if n else None, round(medp) if n and medp == medp else None)
                have = (summary["count"], summary["cancelled"], summary["medianPpm2"],
                        round(summary["medianPrice"]) if summary["medianPrice"] is not None else None)  # fmt: skip
                check("C2 trades 요약", want == have, f"{tag}: 기대 {want} / 응답 {have}")

    # C3 분포
    for _ in range(10):
        s, y, m = rng.choice(sggs), rng.randint(2021, 2026), rng.randint(1, 12)
        if (y, m) > (2026, 9):
            y, m = 2026, 9
        body = api.get(f"/v1/regions/{s}/distribution", params={"ym": f"{y}-{m:02d}"}).json()
        (rep, can, pr, nfloor), = q(f"""SELECT count(), countIf(is_cancelled = 1), countIf({PRICED}),
               countIf(is_cancelled = 0 AND is_outlier = 0 AND floor IS NOT NULL)
               FROM trade_current WHERE sgg_cd = %(s)s AND toYYYYMM(deal_date) = %(ym)s""", {"s": s, "ym": y * 100 + m})  # fmt: skip
        st = body["stats"] or {}
        tag = f"{s} {y}-{m:02d}"
        check("C3 분포 통계", (st.get("reported"), st.get("cancelled"), st.get("sample")) == (rep, can, pr),
              f"{tag}: 기대 {(rep, can, pr)} / 응답 {(st.get('reported'), st.get('cancelled'), st.get('sample'))}")  # fmt: skip
        check("C3 히스토그램 합 = 표본", sum(h["n"] for h in body["histogram"]) == pr, tag)
        check("C3 면적대 합 = 표본", sum(h["n"] for h in body["byArea"]) == pr, tag)
        check("C3 층별 합 = 층 있는 표본", sum(h["n"] for h in body["byFloor"]) == nfloor,
              f"{tag}: 기대 {nfloor} / 응답 {sum(h['n'] for h in body['byFloor'])}")  # fmt: skip
        check("C3 산점도 = 전체", len(body["points"]) == min(rep, 1000), f"{tag}: {len(body['points'])} / {rep}")

    # C4 단지 순위
    for _ in range(6):
        s, y = rng.choice(sggs), rng.randint(2021, 2025)
        body = api.get(f"/v1/regions/{s}/complexes", params={"from": f"{y}-01", "to": f"{y}-12", "limit": "10"}).json()
        exp = q(f"""SELECT complex_key, countIf(is_cancelled = 0) AS n, countIf(is_cancelled = 1),
                   quantileExactInclusiveIf(0.5)(ppm2, {PRICED}) AS med, max(deal_date)
                   FROM trade_current WHERE sgg_cd = %(s)s AND toYear(deal_date) = %(y)s
                   GROUP BY complex_key ORDER BY n DESC, med DESC LIMIT 10""", {"s": s, "y": y})  # fmt: skip
        have = [(i["complexKey"], i["trades"], i["cancelled"], i["medianPpm2"], i["lastDate"]) for i in body["items"]]
        want = [(r[0], r[1], r[2], r1(r[3]), f"{r[4]}") for r in exp]
        check(
            "C4 단지 순위·값",
            have == want,
            f"{s} {y}: 첫 차이 {next((x for x in zip(want, have) if x[0] != x[1]), None)}",
        )

    # C5 시장 개요
    for ym in ("2022-06", "2024-03", "2026-07"):
        body = api.get("/v1/market/overview", params={"ym": ym}).json()
        mo = dt.date(int(ym[:4]), int(ym[5:]), 1)
        ru = {
            r[0]: r[1:]
            for r in q("SELECT region_id, trades, cancelled FROM rollup_month WHERE month = %(m)s", {"m": mo})
        }
        check("C5 전국 거래", (body["nation"]["trades"], body["nation"]["cancelled"]) == tuple(ru["00"]), ym)
        for sd in body["sido"]:
            check(
                "C5 시도 거래", (sd["trades"], sd["cancelled"]) == tuple(ru[sd["regionId"]]), f"{ym} {sd['regionId']}"
            )
        rm = {r[0]: r[1] for r in q("SELECT sgg_cd, trades FROM region_month WHERE month = %(m)s", {"m": mo})}
        bad = [x["sggCd"] for x in body["sgg"] if rm.get(x["sggCd"]) != x["trades"]]
        check(
            "C5 시군구 거래",
            not bad and len(body["sgg"]) == len(rm),
            f"{ym}: 다른 시군구 {bad[:5]} · {len(body['sgg'])}/{len(rm)}",
        )

    # C6 발행(버전 갱신) 뒤 결과 캐시 무효화
    s, mo = sggs[3], dt.date(2025, 5, 1)
    url, params = f"/v1/regions/{s}/months", {"from": "2025-05", "to": "2025-05"}
    before = api.get(url, params=params).json()["items"][0]["trades"]
    ch.command(f"ALTER TABLE region_month UPDATE trades = trades + 1000 WHERE sgg_cd = '{s}' AND month = '{mo}' "
               "SETTINGS mutations_sync = 1")  # fmt: skip
    rd = redis.Redis(host="127.0.0.1", port=6539, username="pipeline", password=e["REDIS_PIPELINE_PASSWORD"])
    old = rd.get("al:ds:ver").decode()
    try:
        cached = api.get(url, params=params).json()["items"][0]["trades"]
        rd.set("al:ds:ver", old + "-qa")
        time.sleep(1.2)  # API 는 버전을 프로세스 안에서 1초 캐시한다 (core/http.py DatasetVersion — 설계)
        fresh = api.get(url, params=params).json()["items"][0]["trades"]
        check("C6 같은 버전이면 캐시 응답", cached == before, f"{cached} / {before}")
        check("C6 버전이 바뀌면 새 값", fresh == before + 1000, f"{fresh} / {before + 1000}")
    finally:
        ch.command(f"ALTER TABLE region_month UPDATE trades = trades - 1000 WHERE sgg_cd = '{s}' AND month = '{mo}' "
                   "SETTINGS mutations_sync = 1")  # fmt: skip
        rd.set("al:ds:ver", old + "-qa2")

    print(f"값 대조 {CHECKS[0]}건 · 실패 {len(FAILS)}건")
    for f in FAILS[:40]:
        print("  FAIL", f)


if __name__ == "__main__":
    main()
