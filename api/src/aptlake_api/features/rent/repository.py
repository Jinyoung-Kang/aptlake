"""전월세 데이터 접근 (ClickHouse, ADR-050). SQL 만 — 규칙은 service 에. 모든 값은 서버측 바인딩({name:Type})."""

from __future__ import annotations

import datetime as dt
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from ...core.values import add_months, month_end
from ..regions.repository import AREA_EXPR
from .service import RentFilter

Row = dict[str, Any]
RENT_COLS = """rent_id, deal_date, complex_key, apt_nm, umd_nm, jibun, area_m2, floor, deposit_manwon,
               monthly_rent_manwon, is_jeonse, deposit_ppm2, contract_type, contract_term, use_rr_right,
               pre_deposit_manwon, pre_monthly_rent_manwon, build_year, is_outlier, version, missing_since"""
MONTH_COLS = """month, contracts, jeonse, wolse, renewals, new_contracts, rr_used, jeonse_priced, outliers,
                jeonse_p25_ppm2, jeonse_median_ppm2, jeonse_p75_ppm2, wolse_median_rent, wolse_median_deposit,
                low_sample"""
WHERE = "sgg_cd = {s:String} AND deal_date BETWEEN {a:Date} AND {b:Date}"
# 표본: 매매는 해제·이상치·면적 0 제외(지역 분위수와 같은 기준), 전세는 이상치·면적 0 제외
TRADE_PRICED = "is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0"
JEONSE_PRICED = "is_jeonse = 1 AND is_outlier = 0 AND area_m2 > 0"


def _window_sql(table: str, value: str, priced: str) -> str:
    """계약 하나를 그 달부터 (창 길이) 개월의 창에 넣어(ARRAY JOIN) 창 끝 달마다 정확 중앙값 — 한 시군구라 수천 행."""
    return f"""
        SELECT m, count() AS n, quantileExactInclusive(0.5)({value}) AS med
        FROM {table}
        ARRAY JOIN arrayMap(i -> addMonths(toStartOfMonth(deal_date), i), range({{w:UInt8}})) AS m
        WHERE {WHERE} AND {priced}
        GROUP BY m HAVING m BETWEEN {{ma:Date}} AND {{mb:Date}} ORDER BY m"""


class RentRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any] | None = None) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params or {})).named_results())

    async def months(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""SELECT {MONTH_COLS} FROM region_rent_month
                WHERE sgg_cd = {{s:String}} AND month BETWEEN {{a:Date}} AND {{b:Date}} ORDER BY month""",
            {"s": sgg, "a": a, "b": b},
        )

    async def window_medians(self, sgg: str, a: dt.date, b: dt.date, months: int) -> tuple[list[Row], list[Row]]:
        """(매매, 전세) 창 끝 달마다 ㎡당 중앙값·표본. a·b 는 창 끝 달의 범위(그 달 1일)."""
        p = {"s": sgg, "a": add_months(a, -(months - 1)), "b": month_end(b), "ma": a, "mb": b, "w": months}
        trade = await self._q(_window_sql("trade_current", "ppm2", TRADE_PRICED), p)
        rent = await self._q(_window_sql("rent_current", "deposit_ppm2", JEONSE_PRICED), p)
        return trade, rent

    async def price_stats(self, sgg: str, a: dt.date, b: dt.date) -> Row:
        rows = await self._q(
            f"""
            SELECT count() AS n, countIf(is_jeonse = 1) AS jeonse, countIf({JEONSE_PRICED}) AS priced,
                   quantileExactInclusiveIf(0.05)(deposit_ppm2, {JEONSE_PRICED}) AS p05,
                   quantileExactInclusiveIf(0.25)(deposit_ppm2, {JEONSE_PRICED}) AS p25,
                   quantileExactInclusiveIf(0.5)(deposit_ppm2, {JEONSE_PRICED}) AS p50,
                   quantileExactInclusiveIf(0.75)(deposit_ppm2, {JEONSE_PRICED}) AS p75,
                   quantileExactInclusiveIf(0.95)(deposit_ppm2, {JEONSE_PRICED}) AS p95,
                   quantileExactInclusiveIf(0.5)(toFloat64(monthly_rent_manwon), is_jeonse = 0) AS rent_med,
                   quantileExactInclusiveIf(0.5)(toFloat64(deposit_manwon), is_jeonse = 0) AS dep_med
            FROM rent_current WHERE {WHERE}""",
            {"s": sgg, "a": a, "b": b},
        )
        return rows[0]

    async def histogram(self, sgg: str, a: dt.date, b: dt.date, width: float) -> list[Row]:
        return await self._q(
            f"""
            SELECT floor(deposit_ppm2 / {{w:Float64}}) * {{w:Float64}} AS lo, count() AS n
            FROM rent_current WHERE {WHERE} AND {JEONSE_PRICED}
            GROUP BY lo ORDER BY lo""",
            {"s": sgg, "a": a, "b": b, "w": width},
        )

    async def by_area(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""
            SELECT {AREA_EXPR} AS band,
                   countIf(is_jeonse = 1) AS jeonse,
                   quantileExactInclusiveIf(0.5)(toFloat64(deposit_manwon), is_jeonse = 1) AS jeonse_dep,
                   quantileExactInclusiveIf(0.5)(deposit_ppm2, is_jeonse = 1) AS jeonse_ppm2,
                   countIf(is_jeonse = 0) AS wolse,
                   quantileExactInclusiveIf(0.5)(toFloat64(monthly_rent_manwon), is_jeonse = 0) AS wolse_rent,
                   quantileExactInclusiveIf(0.5)(toFloat64(deposit_manwon), is_jeonse = 0) AS wolse_dep
            FROM rent_current WHERE {WHERE} AND is_outlier = 0 AND area_m2 > 0
            GROUP BY band ORDER BY min(area_m2)""",
            {"s": sgg, "a": a, "b": b},
        )

    @staticmethod
    def _where(f: RentFilter) -> tuple[list[str], dict[str, Any]]:
        where = ["sgg_cd = {sgg:String}", "deal_date BETWEEN {a:Date} AND {b:Date}"]
        params: dict[str, Any] = {"sgg": f.sgg, "a": f.start, "b": f.end}
        if f.min_area is not None:
            where.append("area_m2 >= {min:Float64}")
            params["min"] = f.min_area
        if f.max_area is not None:
            where.append("area_m2 <= {max:Float64}")
            params["max"] = f.max_area
        if f.kind == "jeonse":
            where.append("is_jeonse = 1")
        elif f.kind == "wolse":
            where.append("is_jeonse = 0")
        return where, params

    async def page(self, f: RentFilter, after: tuple[dt.date, str] | None, limit: int) -> list[Row]:
        where, params = self._where(f)
        params["lim"] = limit
        if after:
            where.append("(deal_date, rent_id) < ({cd:Date}, {ck:String})")
            params["cd"], params["ck"] = after
        return await self._q(
            f"""SELECT {RENT_COLS} FROM rent_current WHERE {" AND ".join(where)}
                ORDER BY deal_date DESC, rent_id DESC LIMIT {{lim:UInt32}}""",
            params,
        )

    async def summary(self, f: RentFilter) -> Row:
        where, params = self._where(f)
        rows = await self._q(
            f"""SELECT count() AS n, countIf(is_jeonse = 1) AS jeonse,
                       quantileExactInclusiveIf(0.5)(toFloat64(deposit_manwon), is_jeonse = 1) AS jeonse_dep,
                       quantileExactInclusiveIf(0.5)(deposit_ppm2, {JEONSE_PRICED}) AS jeonse_ppm2,
                       quantileExactInclusiveIf(0.5)(toFloat64(monthly_rent_manwon), is_jeonse = 0) AS wolse_rent
                FROM rent_current WHERE {" AND ".join(where)}""",
            params,
        )
        return rows[0]

    async def versions(self, rent_id: str, yyyymm: int) -> list[Row]:
        return await self._q(
            """
            SELECT version, valid_from, valid_to, is_current, contract_type, contract_term, use_rr_right,
                   pre_deposit_manwon, pre_monthly_rent_manwon, build_year
            FROM rent_version
            WHERE rent_id = {id:String} AND toYYYYMM(deal_date) = {ym:UInt32}
            ORDER BY valid_from""",
            {"id": rent_id, "ym": yyyymm},
        )

    async def complex_rents(self, sgg: str, key: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""SELECT toFloat64(area_m2) AS area, deposit_manwon, monthly_rent_manwon, is_jeonse, is_outlier, deposit_ppm2
                FROM rent_current WHERE {WHERE} AND complex_key = {{k:String}}""",
            {"s": sgg, "a": a, "b": b, "k": key},
        )

    async def complex_trades(self, sgg: str, key: str, a: dt.date, b: dt.date) -> list[Row]:
        return await self._q(
            f"""SELECT toFloat64(area_m2) AS area, ppm2 FROM trade_current
                WHERE {WHERE} AND complex_key = {{k:String}} AND {TRADE_PRICED}""",
            {"s": sgg, "a": a, "b": b, "k": key},
        )

    async def complex_recent(self, sgg: str, key: str, limit: int) -> list[Row]:
        return await self._q(
            f"""SELECT {RENT_COLS} FROM rent_current
                WHERE sgg_cd = {{s:String}} AND complex_key = {{k:String}}
                ORDER BY deal_date DESC, rent_id DESC LIMIT {{l:UInt32}}""",
            {"s": sgg, "k": key, "l": limit},
        )

    async def latest_month(self) -> dt.date | None:
        """발행된 전월세의 마지막 계약월 (1일). 파티션 최솟값·최댓값 정보로 답해 표를 다 읽지 않는다."""
        rows = await self._q("SELECT toStartOfMonth(max(deal_date)) AS m, count() AS n FROM rent_current")
        return rows[0]["m"] if rows and rows[0]["n"] else None

    async def ratio_by_sgg(self, a: dt.date, b: dt.date) -> tuple[list[Row], list[Row]]:
        """시장 지도: 창(a~b)의 시군구별 (매매, 전세) ㎡당 중앙값·표본 — 전국 3개월 수십만 행."""
        p = {"a": a, "b": b}
        trade = await self._q(
            f"""SELECT sgg_cd, count() AS n, quantileExactInclusive(0.5)(ppm2) AS med FROM trade_current
                WHERE deal_date BETWEEN {{a:Date}} AND {{b:Date}} AND {TRADE_PRICED} GROUP BY sgg_cd""",
            p,
        )
        rent = await self._q(
            f"""SELECT sgg_cd, count() AS n, quantileExactInclusive(0.5)(deposit_ppm2) AS med FROM rent_current
                WHERE deal_date BETWEEN {{a:Date}} AND {{b:Date}} AND {JEONSE_PRICED} GROUP BY sgg_cd""",
            p,
        )
        return trade, rent
