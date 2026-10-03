"""지역 데이터 접근 (ClickHouse). SQL 만 — 규칙은 service 에. 모든 값은 서버측 파라미터 바인딩({name:Type})."""

from __future__ import annotations

import datetime as dt
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from .service import AREA_BANDS, FLOOR_BANDS

Row = dict[str, Any]
REGION_SQL = "SELECT sgg_cd, sido_cd, sido_nm, sgg_nm, full_nm FROM region ORDER BY sgg_cd"
PRICED = "is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0"
WHERE = "sgg_cd = {s:String} AND deal_date BETWEEN {a:Date} AND {b:Date}"

# 시군구 이름표는 작고 발행 시에만 바뀌므로 데이터셋 버전별로 프로세스 메모리에 둔다 (조회당 질의 1회 절약)
_names: dict[str, Any] = {"ver": None, "rows": {}}


def _band_expr(col: str, bands: list[tuple[int | None, int | None]], inclusive_upper: bool) -> str:
    """구간 이름 식 (상수만 조립 — 사용자 입력 없음)."""
    parts = []
    for lo, hi in bands:
        if hi is None:
            parts.append(f"'{lo}+'")
            break
        cond = f"{col} <= {hi}" if inclusive_upper else f"{col} < {hi}"
        name = f"≤{hi}" if lo is None and inclusive_upper else f"<{hi}" if lo is None else f"{lo}–{hi}"
        parts.append(f"{cond}, '{name}'")
    return "multiIf(" + ", ".join(parts) + ")"


AREA_EXPR = _band_expr("toFloat64(area_m2)", AREA_BANDS, inclusive_upper=False)
FLOOR_EXPR = _band_expr("floor", FLOOR_BANDS, inclusive_upper=True)


class RegionRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any] | None = None) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params or {})).named_results())

    async def all(self) -> list[Row]:
        return await self._q(REGION_SQL)

    async def by_code(self, dataset_version: str) -> dict[str, Row]:
        if _names["ver"] != dataset_version or not _names["rows"]:
            _names.update(ver=dataset_version, rows={r["sgg_cd"]: r for r in await self._q(REGION_SQL)})
        return _names["rows"]

    async def months(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            """
            SELECT month, reported, trades, cancelled, priced, outliers, p25_ppm2, median_ppm2, p75_ppm2, low_sample
            FROM region_month
            WHERE sgg_cd = {sgg:String} AND month BETWEEN {a:Date} AND {b:Date}
            ORDER BY month""",
            {"sgg": sgg, "a": a, "b": b},
        )

    async def price_stats(self, sgg: str, a: dt.date, b: dt.date) -> Row:
        rows = await self._q(
            f"""
            SELECT count() AS n, countIf(is_cancelled = 1) AS cancelled,
                   countIf({PRICED}) AS priced,
                   quantileExactInclusiveIf(0.05)(ppm2, {PRICED}) AS p05,
                   quantileExactInclusiveIf(0.25)(ppm2, {PRICED}) AS p25,
                   quantileExactInclusiveIf(0.5)(ppm2, {PRICED}) AS p50,
                   quantileExactInclusiveIf(0.75)(ppm2, {PRICED}) AS p75,
                   quantileExactInclusiveIf(0.95)(ppm2, {PRICED}) AS p95
            FROM trade_current WHERE {WHERE}""",
            {"s": sgg, "a": a, "b": b},
        )
        return rows[0]

    async def histogram(self, sgg: str, a: dt.date, b: dt.date, width: float) -> list[Row]:
        return await self._q(
            f"""
            SELECT floor(ppm2 / {{w:Float64}}) * {{w:Float64}} AS lo, count() AS n
            FROM trade_current WHERE {WHERE} AND {PRICED}
            GROUP BY lo ORDER BY lo""",
            {"s": sgg, "a": a, "b": b, "w": width},
        )

    async def by_area(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""
            SELECT {AREA_EXPR} AS band, count() AS n,
                   quantileExactInclusive(0.5)(ppm2) AS median_ppm2, quantileExactInclusive(0.5)(price_manwon) AS median_price
            FROM trade_current WHERE {WHERE} AND {PRICED}
            GROUP BY band ORDER BY min(area_m2)""",
            {"s": sgg, "a": a, "b": b},
        )

    async def by_floor(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""
            SELECT {FLOOR_EXPR} AS band, count() AS n, quantileExactInclusive(0.5)(ppm2) AS median_ppm2
            FROM trade_current WHERE {WHERE} AND is_cancelled = 0 AND is_outlier = 0 AND floor IS NOT NULL
            GROUP BY band ORDER BY min(floor)""",
            {"s": sgg, "a": a, "b": b},
        )

    async def points(self, sgg: str, a: dt.date, b: dt.date, limit: int) -> list[Row]:
        return await self._q(
            f"""
            SELECT toFloat64(area_m2) AS area, price_manwon AS price, floor, is_cancelled AS c, is_outlier AS o
            FROM trade_current WHERE {WHERE} ORDER BY deal_date LIMIT {{l:UInt32}}""",
            {"s": sgg, "a": a, "b": b, "l": limit},
        )

    async def top_complexes(self, sgg: str, a: dt.date, b: dt.date, limit: int) -> list[Row]:
        return await self._q(
            f"""
            SELECT complex_key, any(apt_nm) AS apt, any(umd_nm) AS umd, any(build_year) AS built,
                   countIf(is_cancelled = 0) AS n, countIf(is_cancelled = 1) AS cancelled,
                   quantileExactInclusiveIf(0.5)(ppm2, {PRICED}) AS med,
                   max(deal_date) AS last_date,
                   -- 최근 거래 = 신고된 마지막 거래 — 해제된 거래도 포함한다(의도, ADR-037). 화면 머리글에 '해제 포함' 표시
                   -- 같은 날 거래가 여럿이면 거래 ID 로 하나를 정한다 (그냥 deal_date 면 엔진·병합 순서에 따라 바뀜)
                   argMax(price_manwon, (deal_date, trade_id)) AS last_price,
                   argMax(toFloat64(area_m2), (deal_date, trade_id)) AS last_area
            FROM trade_current
            WHERE {WHERE}
            GROUP BY complex_key ORDER BY n DESC, med DESC LIMIT {{l:UInt16}}""",
            {"s": sgg, "a": a, "b": b, "l": limit},
        )
