"""QA 스택 시험 데이터 생성기 — 운영 데이터는 쓰지 않고, 운영과 같은 모양·규모의 가짜 데이터를 결정적으로 만든다.

  cd api && uv run python ../qa/seed/seed.py [--sgg 256] [--months 69]

- 대상: QA 스택만 (ClickHouse 127.0.0.1:8740 · PostgreSQL 5592 · Redis 6539, 비밀값은 qa/.env.qa). 운영 포트에는 붙지 않는다.
- ClickHouse: 거래(약 시군구 × 월 × 평균 155건)를 서버 안에서 해시로 만든 뒤, 집계 표는 dbt gold 와 같은 정의로 거래 표에서 계산한다
  (region_month: 해제 제외·면적 0·이상치 제외 표본, 표본 < 5 면 분위수 없음).
- 경계·공격 사례: 조작 문자열 단지명·동 이름(XSS·SQL·템플릿·아주 긴 이름·이모지·제어 문자), 면적 0, 층 NULL·지하,
  해제·이상치·사라진 거래, 비밀값 모양 문자열이 든 수집 오류(가림 확인용).
- 다시 실행하면 표를 비우고 처음부터 같은 데이터를 만든다 (멱등).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import clickhouse_connect
import psycopg
import redis

ROOT = Path(__file__).resolve().parents[2]
# 시드마다 버전 번호를 올린다 — 같은 버전이면 버전을 키에 넣는 API 결과 캐시에 이전 데이터 응답이 남는다 (발행과 같게)
VERSION = f"gold@2026-10-02.{int(time.time()) % 1_000_000}"
AS_OF = dt.datetime(2026, 10, 2, 0, 0, tzinfo=dt.UTC)  # 실제 시각보다 과거 (미래 시각이면 비우기·신선도 판단이 어긋남)
SIDO = [("11", "서울특별시"), ("26", "부산광역시"), ("27", "대구광역시"), ("28", "인천광역시"), ("29", "광주광역시"),
        ("30", "대전광역시"), ("31", "울산광역시"), ("36", "세종특별자치시"), ("41", "경기도"), ("43", "충청북도"),
        ("44", "충청남도"), ("46", "전라남도"), ("47", "경상북도"), ("48", "경상남도"), ("50", "제주특별자치도"),
        ("51", "강원특별자치도"), ("52", "전북특별자치도")]  # fmt: skip
# 화면·API 에 그대로 나가는 문자열 — 실행되거나 질의를 깨면 결함
ATTACK = [
    "<script>alert('qa-xss-1')</script>",
    '"><img src=x onerror=alert(2)>',
    "'; DROP TABLE trade_current; --",
    "{{7*7}}${7*7}<%= 7*7 %>",
    "<svg/onload=alert(3)>",
    "javascript:alert(4)",
    "가" * 200,
    "😀🏢 이모지 단지 ‮ 오른쪽에서왼쪽",
    "줄\n바꿈\t탭\\역슬래시",
    "%27%22%3C%3E 백분율 인코딩",
]
SECRETY = "원천 호출 실패: https://apis.data.go.kr/x?serviceKey=QAFAKESECRET1234567890&LAWD_CD=11110 password=hunter2"


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / "qa/.env.qa").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def regions(n: int) -> list[tuple[str, str, str, str, str]]:
    out = []
    for i in range(n):
        sido_cd, sido_nm = SIDO[i % len(SIDO)]
        k = i // len(SIDO)
        sgg_cd = f"{sido_cd}{110 + k * 20:03d}"
        sgg_nm = f"QA{k + 1}구" if i else "QA종로구"
        if i == 5:  # 지역 이름에도 조작 문자열 (지도 툴팁·검색·표)
            sgg_nm = "<b>QA</b><img src=x onerror=alert('sgg')>구"
        out.append((sgg_cd, sido_cd, sido_nm, sgg_nm, f"{sido_nm} {sgg_nm}"))
    return out


def seed_clickhouse(c, regs, months: int) -> None:
    for t in ("region", "region_month", "rollup_month", "trade_current", "trade_version", "complex", "price_index",
              "index_reference", "index_validation", "usage_event") + tuple(
                  f"{t}_staging" for t in ("region_month", "rollup_month", "trade_current", "trade_version")):  # fmt: skip
        c.command(f"TRUNCATE TABLE aptlake.{t}")
    c.insert("aptlake.region", [list(r) for r in regs], column_names=["sgg_cd", "sido_cd", "sido_nm", "sgg_nm", "full_nm"])
    attack = "[" + ", ".join("'" + a.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\t", "\\t") + "'"
                             for a in ATTACK) + "]"  # fmt: skip
    # 거래: (시군구, 월)마다 20~289건, 단지 180개/시군구. h 는 행마다의 결정적 난수
    c.command(f"""
    INSERT INTO aptlake.trade_current
    WITH cityHash64(sgg, ym, k) AS h, cityHash64(sgg, ym, k, 'complex') % 180 AS cx,  -- 단지는 따로 뽑는다 (h 의 나머지와 얽히면 해제가 특정 단지에 몰림)
         toDecimal64(arrayElement([39.9, 49.5, 59.97, 74.8, 84.98, 101.2, 114.5, 134.8, 165.3], 1 + h % 9), 4) AS a0,
         if(h % 4999 = 0, toDecimal64(0, 4), a0) AS area,
         (300 + cityHash64(sgg) % 2500) * (1 + mi * 0.004) * (0.8 + (intDiv(h, 7) % 400) / 1000) AS base,
         toUInt32(round(toFloat64(a0) * base)) AS price,
         h % 18 = 0 AS cancelled,
         d0 + toIntervalDay(intDiv(h, 13) % toDayOfMonth(toLastDayOfMonth(d0))) AS deal
    SELECT
        concat(sgg, '-', toString(ym), '-', leftPad(lower(hex(h)), 16, '0'), '-0'),
        sgg, deal,
        concat('c_', leftPad(lower(hex(cityHash64(sgg, cx))), 16, '0'), leftPad(lower(hex(cityHash64(cx, sgg) % 65536)), 4, '0')),
        if(sgg = '{regs[0][0]}' AND cx < {len(ATTACK)}, {attack}[cx + 1], concat('QA', toString(cx), '단지')),
        if(sgg = '{regs[0][0]}' AND cx = 3, '"><svg onload=alert(5)>동', concat('QA', toString(cx % 12), '동')),
        concat(toString(cx), '-1'),
        area,
        multiIf(h % 17 = 0, NULL, h % 101 = 0, toInt16(-1), toInt16(1 + h % 35)),
        price,
        if(area > 0, price / toFloat64(area), 0),
        cancelled,
        if(cancelled, deal + 20, NULL),
        if(h % 3 = 0, NULL, deal + 30),
        if(h % 5 = 0, concat(toString(100 + h % 20), '동'), ''),
        if(h % 9 = 0, '직거래', '중개거래'),
        if(h % 11 = 0, '법인', '개인'),
        if(h % 13 = 0, '법인', '개인'),
        if(h % 23 = 0, NULL, toUInt16(1980 + cx % 45)),
        h % 997 = 0,
        if(cancelled, 2, 1),
        toDateTime64('2026-10-01 00:00:00', 3, 'UTC'),
        if(h % 1009 = 0, toDateTime64('2026-09-15 00:00:00', 3, 'UTC'), NULL)
    FROM (
        SELECT r.sgg_cd AS sgg, m.mi AS mi, m.d0 AS d0, toYYYYMM(m.d0) AS ym, 20 + cityHash64(r.sgg_cd, ym) % 270 AS n
        FROM aptlake.region AS r
        CROSS JOIN (SELECT number AS mi, addMonths(toDate('2021-01-01'), number) AS d0 FROM numbers({months})) AS m
    ) ARRAY JOIN range(n) AS k
    SETTINGS max_partitions_per_insert_block = 1000""")
    c.command("""
    INSERT INTO aptlake.trade_version
    SELECT trade_id, deal_date, 1, valid_from - toIntervalDay(30), if(is_cancelled, valid_from, NULL), NOT is_cancelled, 0,
           NULL, registered_date, apt_dong, deal_kind, seller_type, buyer_type FROM aptlake.trade_current
    UNION ALL
    SELECT trade_id, deal_date, 2, valid_from, NULL, 1, 1, cancel_date, registered_date, apt_dong, deal_kind, seller_type,
           buyer_type FROM aptlake.trade_current WHERE is_cancelled
    SETTINGS max_partitions_per_insert_block = 1000""")
    agg = """count() AS reported, countIf(NOT is_cancelled) AS trades, countIf(is_cancelled) AS cancelled,
             countIf(NOT is_cancelled AND area_m2 > 0 AND NOT is_outlier) AS priced, countIf(is_outlier) AS outliers,
             quantileExactInclusiveIf(0.25)(ppm2, NOT is_cancelled AND area_m2 > 0 AND NOT is_outlier) AS q1,
             quantileExactInclusiveIf(0.5)(ppm2, NOT is_cancelled AND area_m2 > 0 AND NOT is_outlier) AS q2,
             quantileExactInclusiveIf(0.75)(ppm2, NOT is_cancelled AND area_m2 > 0 AND NOT is_outlier) AS q3"""
    pick = f"""reported, trades, cancelled, priced, outliers, if(priced >= 5, q1, NULL), if(priced >= 5, q2, NULL),
               if(priced >= 5, q3, NULL), priced < 5, '{VERSION}'"""
    c.command(f"""INSERT INTO aptlake.region_month
        SELECT sgg_cd, month, {pick} FROM (
            SELECT sgg_cd, toStartOfMonth(deal_date) AS month, {agg} FROM aptlake.trade_current GROUP BY sgg_cd, month)""")
    c.command(f"""INSERT INTO aptlake.rollup_month
        SELECT region_id, level, month, {pick} FROM (
            SELECT substring(sgg_cd, 1, 2) AS region_id, 'sido' AS level, toStartOfMonth(deal_date) AS month, {agg}
            FROM aptlake.trade_current GROUP BY region_id, month
            UNION ALL
            SELECT '00', 'nation', toStartOfMonth(deal_date) AS month, {agg} FROM aptlake.trade_current GROUP BY month)""")
    c.command("""INSERT INTO aptlake.complex
        SELECT complex_key, any(sgg_cd), any(umd_nm), any(jibun), any(apt_nm), any(build_year), 0, min(deal_date), count()
        FROM aptlake.trade_current GROUP BY complex_key""")
    ids = ["00"] + [s for s, _ in SIDO]
    c.command(f"""INSERT INTO aptlake.price_index
        SELECT rid, 'HEDONIC_TD_v1', addMonths(toDate('2021-01-01'), number), v, v * 0.985, v * 1.015, n, 'qa' FROM (
            SELECT rid, number, 800 + cityHash64(rid, number) % 4000 AS n,
                   100 * exp(sum(0.004 + (toInt64(cityHash64(rid, number) % 200) - 100) / 10000)
                             OVER (PARTITION BY rid ORDER BY number)) AS v
            FROM numbers({months - 1}) ARRAY JOIN {ids} AS rid)""")
    c.command("""INSERT INTO aptlake.index_reference
        SELECT region_id, period, index_value * (0.98 + (cityHash64(region_id, period) % 40) / 1000), 'R-ONE'
        FROM aptlake.price_index""")
    c.command(f"""INSERT INTO aptlake.index_validation
        SELECT rid, 'HEDONIC_TD_v1', 'R-ONE', 0.6 + (cityHash64(rid) % 35) / 100, 0.7 + (cityHash64(rid, 1) % 25) / 100,
               {months - 2}, toDate('2021-02-01'), addMonths(toDate('2021-01-01'), {months - 2})
        FROM (SELECT arrayJoin({ids}) AS rid)""")


def square(i: int) -> dict:
    x, y = 126.0 + (i % 16) * 0.2, 34.0 + (i // 16) * 0.2
    ring = [[x, y], [x + 0.18, y], [x + 0.18, y + 0.18], [x, y + 0.18], [x, y]]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def seed_postgres(dsn: str, regs, months: int) -> None:
    yms = [f"{2021 + m // 12}{m % 12 + 1:02d}" for m in range(months)]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("""TRUNCATE ops.region, ops.region_boundary, ops.ingest_partition, ops.month_state, ops.dataset_version,
                     ops.dq_result, ops.api_budget""")  # fmt: skip
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO ops.region VALUES (%s,%s,%s,%s,%s,true,true,'qa-seed',%s)",
                [(*r, AS_OF) for r in regs],
            )
            cur.executemany(
                "INSERT INTO ops.region_boundary VALUES (%s,%s,0.001,'qa-seed',%s,'{}',%s)",
                [(r[0], json.dumps(square(i)), "0" * 64, AS_OF) for i, r in enumerate(regs)],
            )
            rows = []
            for i, r in enumerate(regs):
                for j, ym in enumerate(yms):
                    h = (i * 7919 + j * 104729) % 1000
                    status, err = "MERGED", None
                    if j == len(yms) - 1:
                        status = "PENDING"
                    elif h < 25:
                        status, err = "RETRY", SECRETY if h % 2 else "HTTP 503 <script>alert('err')</script>"
                    elif h < 32:
                        status, err = "QUARANTINED", "행 수 급감: 이전 120 → 이번 3 (검사 실패)"
                    elif h < 35:
                        status = "FETCHING"
                    rows.append((r[0], ym, status, 1 + h % 3, 100 + h % 150, 100 + h % 140, err, AS_OF))
            cur.executemany(
                """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, attempts, rows_last, rows_prev, last_error,
                   last_fetched_at, fetch_count) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1)""",
                rows,
            )
            cur.executemany(
                "INSERT INTO ops.month_state VALUES (%s,false,1,%s,%s,%s)",
                [(ym, AS_OF, VERSION, AS_OF) for ym in yms[:-1]],
            )
            c.execute(
                "INSERT INTO ops.dataset_version VALUES (%s,%s,%s,%s,%s,%s,'qa-seed')",
                (VERSION, AS_OF, AS_OF, yms[:-1], json.dumps({"silver": 1}), json.dumps({"trade_current": 0})),
            )
            dq = []  # silver 검사는 partition = 계약월(YYYYMM), 전체 검사는 partition 없음 (ops_db.record_dq 와 같게)
            for j, ym in enumerate(yms[:-1]):
                for name in ("row_count_drop", "duplicate_keys", "null_ratio"):
                    failed = (j * 3 + len(name)) % 23 == 0
                    dq.append(("silver.apt_trade", ym, name, not failed, "ERROR" if name == "duplicate_keys" else "WARN",
                               failed and name == "duplicate_keys",
                               json.dumps({"prev": 120, "now": 3 if failed else 118, "note": ATTACK[0]}), "qa-run", AS_OF))  # fmt: skip
            dq.append(("gold.region_month", None, "sum_matches_rollup", False, "ERROR", True,
                       json.dumps({"detail": SECRETY}), "qa-run", AS_OF))  # fmt: skip
            cur.executemany("INSERT INTO ops.dq_result (asset, partition, check_name, passed, severity, blocking, metric,"
                            " run_id, at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", dq)  # fmt: skip
            c.execute(
                "INSERT INTO ops.api_budget (day, source, limit_calls, used_calls) VALUES (CURRENT_DATE, 'rtms', 10000, 1234)"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sgg", type=int, default=256)
    ap.add_argument("--months", type=int, default=69)
    a = ap.parse_args()
    e = env()
    regs = regions(a.sgg)
    ch = clickhouse_connect.get_client(host="127.0.0.1", port=8740, username="admin",
                                       password=e["CLICKHOUSE_ADMIN_PASSWORD"], send_receive_timeout=900)  # fmt: skip
    seed_clickhouse(ch, regs, a.months)
    seed_postgres(f"postgresql://postgres:{e['POSTGRES_PASSWORD']}@127.0.0.1:5592/aptlake", regs, a.months)
    r = redis.Redis(host="127.0.0.1", port=6539, username="pipeline", password=e["REDIS_PIPELINE_PASSWORD"])
    r.mset({"al:ds:ver": VERSION, "al:ds:asof": AS_OF.isoformat()})
    counts = {t: ch.query(f"SELECT count() FROM aptlake.{t}").first_row[0]
              for t in ("region", "trade_current", "trade_version", "region_month", "rollup_month", "complex", "price_index")}  # fmt: skip
    print("QA 시험 데이터:", counts)


if __name__ == "__main__":
    main()
