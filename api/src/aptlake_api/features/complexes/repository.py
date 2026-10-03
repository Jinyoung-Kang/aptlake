"""단지 데이터 접근 (ClickHouse, 서버측 바인딩)."""

from __future__ import annotations

from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

from ..trades.repository import TRADE_COLS

Row = dict[str, Any]


class ComplexRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any]) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params)).named_results())

    async def get(self, key: str) -> Row | None:
        rows = await self._q(
            """SELECT complex_key, sgg_cd, umd_nm, jibun, apt_nm, build_year, land_leasehold,
                      first_seen, trades FROM complex WHERE complex_key = {k:String}""",
            {"k": key},
        )
        return rows[0] if rows else None

    async def recent_trades(self, sgg: str, key: str, limit: int) -> list[Row]:
        return await self._q(
            f"""SELECT {TRADE_COLS} FROM trade_current
                WHERE sgg_cd = {{sgg:String}} AND complex_key = {{k:String}}
                ORDER BY deal_date DESC, trade_id DESC LIMIT {{l:UInt32}}""",
            {"sgg": sgg, "k": key, "l": limit},
        )

    async def trade_points(self, sgg: str, key: str, limit: int) -> list[Row]:
        return await self._q(
            """SELECT deal_date, toFloat64(area_m2) AS area, floor, price_manwon, ppm2, is_cancelled, is_outlier
               FROM trade_current WHERE sgg_cd = {sgg:String} AND complex_key = {k:String}
               ORDER BY deal_date LIMIT {l:UInt32}""",
            {"sgg": sgg, "k": key, "l": limit},
        )

    async def search(self, term: str, limit: int) -> list[Row]:
        return await self._q(
            """
            SELECT complex_key, sgg_cd, umd_nm, apt_nm, build_year, trades FROM complex
            WHERE positionCaseInsensitiveUTF8(apt_nm, {q:String}) > 0
               OR positionCaseInsensitiveUTF8(umd_nm, {q:String}) > 0
            ORDER BY trades DESC LIMIT {l:UInt32}""",
            {"q": term, "l": limit},
        )
