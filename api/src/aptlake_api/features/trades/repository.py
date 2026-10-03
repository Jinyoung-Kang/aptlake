"""거래 데이터 접근 (ClickHouse). WHERE 는 고정된 조각만 조립하고 값은 모두 서버측 바인딩."""

from __future__ import annotations

import datetime as dt
from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from .service import TradeFilter

Row = dict[str, Any]
TRADE_COLS = """trade_id, deal_date, complex_key, apt_nm, umd_nm, jibun, area_m2, floor, price_manwon, ppm2,
                is_cancelled, cancel_date, registered_date, apt_dong, deal_kind, seller_type, buyer_type,
                build_year, is_outlier, version, missing_since"""


def _where(f: TradeFilter) -> tuple[list[str], dict[str, Any]]:
    where = ["sgg_cd = {sgg:String}", "deal_date BETWEEN {a:Date} AND {b:Date}"]
    params: dict[str, Any] = {"sgg": f.sgg, "a": f.start, "b": f.end}
    if f.min_area is not None:
        where.append("area_m2 >= {min:Float64}")
        params["min"] = f.min_area
    if f.max_area is not None:
        where.append("area_m2 <= {max:Float64}")
        params["max"] = f.max_area
    if not f.include_cancelled:
        where.append("is_cancelled = 0")
    return where, params


class TradeRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any]) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params)).named_results())

    async def page(self, f: TradeFilter, after: tuple[dt.date, str] | None, limit: int) -> list[Row]:
        where, params = _where(f)
        params["lim"] = limit
        if after:
            where.append("(deal_date, trade_id) < ({cd:Date}, {ck:String})")
            params["cd"], params["ck"] = after
        return await self._q(
            f"""SELECT {TRADE_COLS} FROM trade_current WHERE {" AND ".join(where)}
                ORDER BY deal_date DESC, trade_id DESC LIMIT {{lim:UInt32}}""",
            params,
        )

    async def summary(self, f: TradeFilter) -> Row:
        where, params = _where(f)
        rows = await self._q(
            f"""SELECT count() AS n, countIf(is_cancelled = 1) AS cancelled,
                       quantileExactInclusiveIf(0.5)(ppm2, is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0) AS med,
                       quantileExactInclusiveIf(0.5)(price_manwon, is_cancelled = 0) AS med_price
                FROM trade_current WHERE {" AND ".join(where)}""",
            params,
        )
        return rows[0]

    async def versions(self, trade_id: str, yyyymm: int) -> list[Row]:
        return await self._q(
            """
            SELECT version, valid_from, valid_to, is_current, is_cancelled, cancel_date, registered_date,
                   apt_dong, deal_kind, seller_type, buyer_type
            FROM trade_version
            WHERE trade_id = {id:String} AND toYYYYMM(deal_date) = {ym:UInt32}
            ORDER BY valid_from""",
            {"id": trade_id, "ym": yyyymm},
        )
