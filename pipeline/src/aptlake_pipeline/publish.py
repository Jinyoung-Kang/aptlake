"""ClickHouse 발행 (기획서 그림 2).

월 파티션: Trino gold → *_staging 파티션 적재 → 건수·합계 대조 → REPLACE PARTITION (원자적 교체).
대조가 틀리면 예외 → 서빙 테이블은 이전 데이터 그대로 (부분 발행 없음).
차원·지수(소형): 새 테이블에 적재 → 대조 → EXCHANGE TABLES (원자적 교체).
발행이 끝나면 데이터셋 버전을 올리고(ops.dataset_version + Redis), API 캐시 키가 버전을 포함하므로
이전 결과 캐시는 자연히 무효가 된다.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from typing import Any

import clickhouse_connect
import redis
from clickhouse_connect.driver.client import Client

from . import ops_db
from .config import settings
from .lake import current_snapshot_id, execute

DB = "aptlake"
_YM = re.compile(r"\d{6}")


class ReconciliationError(Exception):
    pass


def ch() -> Client:
    s = settings()
    return clickhouse_connect.get_client(
        host=s.ch_host,
        port=s.ch_port,
        username="publisher",
        password=str(s.ch_publisher_password),
        database=DB,
        send_receive_timeout=600,
    )


def _month_date(ym: str) -> dt.date:
    return dt.date(int(ym[:4]), int(ym[4:]), 1)


def _rows(sql: str) -> list[tuple]:
    return execute(sql, user="pipeline")


def _replace_partition(
    client: Client,
    table: str,
    partition: str,
    rows: list[list[Any]],
    columns: list[str],
    checks: dict[str, str],
    expected: dict[str, Any],
) -> int:
    staging = f"{DB}.{table}_staging"
    client.command(f"ALTER TABLE {staging} DROP PARTITION {partition}")
    if rows:
        client.insert(f"{table}_staging", rows, column_names=columns)
    got = client.query(
        f"SELECT {', '.join(f'{e} AS {k}' for k, e in checks.items())} "
        f"FROM {staging} WHERE toYYYYMM({_part_col(table)}) = {partition}"
    ).first_row
    assert got is not None
    actual = dict(zip(checks, got, strict=True))
    for k, v in expected.items():
        if (actual[k] or 0) != (v or 0):
            client.command(f"ALTER TABLE {staging} DROP PARTITION {partition}")
            raise ReconciliationError(f"{table} {partition}: {k} trino={v} clickhouse={actual[k]}")
    if rows:
        client.command(f"ALTER TABLE {DB}.{table} REPLACE PARTITION {partition} FROM {staging}")
    else:
        client.command(f"ALTER TABLE {DB}.{table} DROP PARTITION {partition}")
    client.command(f"ALTER TABLE {staging} DROP PARTITION {partition}")
    return len(rows)


def _part_col(table: str) -> str:
    return {"region_month": "month", "trade_current": "deal_date", "trade_version": "deal_date"}[table]


def publish_month(deal_ym: str, dataset_ver: str) -> dict[str, int]:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    client = ch()
    counts: dict[str, int] = {}

    rm = _rows(f"""SELECT sgg_cd, deal_ym, reported, trades, cancelled, priced, outliers,
                          p25_ppm2, median_ppm2, p75_ppm2, low_sample
                   FROM lake.gold.region_month WHERE deal_ym = '{deal_ym}'""")
    counts["region_month"] = _replace_partition(
        client,
        "region_month",
        deal_ym,
        [
            [r[0], _month_date(r[1]), r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], int(r[10]), dataset_ver]
            for r in rm
        ],
        [
            "sgg_cd",
            "month",
            "reported",
            "trades",
            "cancelled",
            "priced",
            "outliers",
            "p25_ppm2",
            "median_ppm2",
            "p75_ppm2",
            "low_sample",
            "dataset_ver",
        ],
        {"n": "count()", "trades": "sum(trades)", "cancelled": "sum(cancelled)"},
        {"n": len(rm), "trades": sum(r[3] for r in rm), "cancelled": sum(r[4] for r in rm)},
    )

    ts = _rows(f"""SELECT trade_id, sgg_cd, deal_date, complex_key, apt_nm, umd_nm, coalesce(jibun, ''), area_m2,
                          floor, price_manwon, coalesce(ppm2, 0), is_cancelled, cancel_date, registered_date,
                          coalesce(apt_dong, ''), coalesce(deal_kind, ''), coalesce(seller_type, ''),
                          coalesce(buyer_type, ''), build_year, is_outlier, version, valid_from, missing_since
                   FROM lake.gold.trade_serving WHERE deal_ym = '{deal_ym}'""")
    counts["trade_current"] = _replace_partition(
        client,
        "trade_current",
        deal_ym,
        [
            [
                r[0],
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
                r[6],
                Decimal(str(r[7])) if r[7] is not None else Decimal(0),
                r[8],
                r[9],
                float(r[10]),
                int(r[11]),
                r[12],
                r[13],
                r[14],
                r[15],
                r[16],
                r[17],
                r[18],
                int(r[19]),
                r[20],
                r[21],
                r[22],
            ]
            for r in ts
        ],
        [
            "trade_id",
            "sgg_cd",
            "deal_date",
            "complex_key",
            "apt_nm",
            "umd_nm",
            "jibun",
            "area_m2",
            "floor",
            "price_manwon",
            "ppm2",
            "is_cancelled",
            "cancel_date",
            "registered_date",
            "apt_dong",
            "deal_kind",
            "seller_type",
            "buyer_type",
            "build_year",
            "is_outlier",
            "version",
            "valid_from",
            "missing_since",
        ],
        {"n": "count()", "price": "sum(price_manwon)", "cancelled": "sum(is_cancelled)"},
        {"n": len(ts), "price": sum(r[9] for r in ts), "cancelled": sum(int(r[11]) for r in ts)},
    )

    tv = _rows(f"""SELECT trade_id, deal_date, version, valid_from, valid_to, is_current, is_cancelled,
                          cancel_date, registered_date, coalesce(apt_dong, ''), coalesce(deal_kind, ''),
                          coalesce(seller_type, ''), coalesce(buyer_type, '')
                   FROM lake.gold.trade_version WHERE deal_ym = '{deal_ym}'""")
    counts["trade_version"] = _replace_partition(
        client,
        "trade_version",
        deal_ym,
        [[r[0], r[1], r[2], r[3], r[4], int(r[5]), int(r[6]), r[7], r[8], r[9], r[10], r[11], r[12]] for r in tv],
        [
            "trade_id",
            "deal_date",
            "version",
            "valid_from",
            "valid_to",
            "is_current",
            "is_cancelled",
            "cancel_date",
            "registered_date",
            "apt_dong",
            "deal_kind",
            "seller_type",
            "buyer_type",
        ],
        {"n": "count()", "current": "sum(is_current)"},
        {"n": len(tv), "current": sum(int(r[5]) for r in tv)},
    )
    return counts


def _exchange(client: Client, table: str, columns: list[str], rows: list[list[Any]]) -> int:
    """소형 테이블 전체 교체: {table}_next 에 적재 → 건수 확인 → EXCHANGE TABLES."""
    nxt = f"{DB}.{table}_next"
    client.command(f"DROP TABLE IF EXISTS {nxt}")
    client.command(f"CREATE TABLE {nxt} AS {DB}.{table}")
    if rows:
        client.insert(f"{table}_next", rows, column_names=columns)
    first = client.query(f"SELECT count() FROM {nxt}").first_row
    n = first[0] if first else 0
    if n != len(rows):
        raise ReconciliationError(f"{table}: expected {len(rows)} got {n}")
    client.command(f"EXCHANGE TABLES {DB}.{table} AND {nxt}")
    client.command(f"DROP TABLE {nxt}")
    return n


def publish_dimensions() -> dict[str, int]:
    client = ch()
    with ops_db.conn() as c:
        regions = c.execute("""SELECT sgg_cd, sido_cd, sido_nm, sgg_nm, full_nm FROM ops.region
                               WHERE is_leaf AND active ORDER BY sgg_cd""").fetchall()
    out = {
        "region": _exchange(
            client,
            "region",
            ["sgg_cd", "sido_cd", "sido_nm", "sgg_nm", "full_nm"],
            [[r["sgg_cd"], r["sido_cd"], r["sido_nm"], r["sgg_nm"], r["full_nm"]] for r in regions],
        )
    }
    cx = _rows("""SELECT complex_key, sgg_cd, umd_nm, coalesce(jibun, ''), apt_nm, build_year, land_leasehold,
                         first_seen, trades FROM lake.gold.complex_summary""")
    out["complex"] = _exchange(
        client,
        "complex",
        ["complex_key", "sgg_cd", "umd_nm", "jibun", "apt_nm", "build_year", "land_leasehold", "first_seen", "trades"],
        [[r[0], r[1], r[2], r[3], r[4], r[5], int(r[6]), r[7], r[8]] for r in cx],
    )
    return out


def publish_index(points: list[dict], reference: list[dict], validation: list[dict]) -> dict[str, int]:
    client = ch()
    return {
        "price_index": _exchange(
            client,
            "price_index",
            ["region_id", "method", "period", "index_value", "ci_low", "ci_high", "n_obs", "model_ver"],
            [
                [
                    p["region_id"],
                    p["method"],
                    _month_date(p["period"]),
                    p["index_value"],
                    p["ci_low"],
                    p["ci_high"],
                    p["n_obs"],
                    p["model_ver"],
                ]
                for p in points
            ],
        ),
        "index_reference": _exchange(
            client,
            "index_reference",
            ["region_id", "period", "value", "source"],
            [[r["region_id"], _month_date(r["period"]), r["value"], r["source"]] for r in reference],
        ),
        "index_validation": _exchange(
            client,
            "index_validation",
            ["region_id", "method", "reference", "corr_mom", "direction_match", "n_months", "window_from", "window_to"],
            [
                [
                    v["region_id"],
                    v["method"],
                    v["reference"],
                    v["corr_mom"],
                    v["direction_match"],
                    v["n_months"],
                    _month_date(v["window"][0]) if v["window"] else dt.date(1970, 1, 1),
                    _month_date(v["window"][1]) if v["window"] else dt.date(1970, 1, 1),
                ]
                for v in validation
            ],
        ),
    }


def next_dataset_version(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(tz=dt.UTC)
    day = now.astimezone(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    with ops_db.conn() as c:
        row = c.execute(
            "SELECT count(*) AS n FROM ops.dataset_version WHERE version LIKE %s", (f"gold@{day}.%",)
        ).fetchone()
    n = row["n"] if row else 0
    return f"gold@{day}.{n + 1}"


def commit_version(version: str, partitions: list[str], row_counts: dict[str, int], run_id: str | None) -> None:
    with ops_db.conn() as c:
        row = c.execute("SELECT max(last_fetched_at) AS t FROM ops.ingest_partition").fetchone()
    as_of = (row["t"] if row else None) or dt.datetime.now(tz=dt.UTC)
    snapshots = {
        t: current_snapshot_id(t)
        for t in (
            "bronze.rtms_raw",
            "silver.apt_trade",
            "gold.region_month",
            "gold.trade_serving",
            "gold.trade_version",
        )
    }
    ops_db.record_dataset_version(version, as_of, partitions, snapshots, row_counts, run_id)
    r = redis.Redis.from_url(str(settings().redis_url))
    r.mset({"al:ds:ver": version, "al:ds:asof": as_of.isoformat()})
