"""시장 개요·시세 띠·경계 데이터 접근. 집계는 ClickHouse(서버측 바인딩), 경계는 운영 DB(PostgreSQL)."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]


class MarketRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any] | None = None) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params or {})).named_results())

    async def month_bounds(self) -> tuple[dt.date | None, dt.date | None]:
        rows = await self._q("SELECT min(month) AS a, max(month) AS b FROM region_month")
        return (rows[0]["a"], rows[0]["b"]) if rows and rows[0]["b"] else (None, None)

    async def sgg_months(self, m: dt.date, pm: dt.date, py: dt.date) -> list[Row]:
        return await self._q(
            """
            SELECT sgg_cd, month, trades, cancelled, priced, median_ppm2, low_sample
            FROM region_month WHERE month IN ({m:Date}, {pm:Date}, {py:Date})""",
            {"m": m, "pm": pm, "py": py},
        )

    async def rollups(self, a: dt.date, b: dt.date, py: dt.date) -> list[Row]:
        return await self._q(
            """
            SELECT region_id, month, trades, cancelled, priced, p25_ppm2, median_ppm2, p75_ppm2, low_sample
            FROM rollup_month WHERE month BETWEEN {a:Date} AND {b:Date} OR month = {py:Date}
            ORDER BY region_id, month""",
            {"a": a, "b": b, "py": py},
        )

    async def nation_months(self, m: dt.date, pm: dt.date, py: dt.date) -> list[Row]:
        return await self._q(
            """SELECT month, trades, cancelled, median_ppm2 FROM rollup_month
               WHERE region_id = '00' AND month IN ({m:Date}, {pm:Date}, {py:Date})""",
            {"m": m, "pm": pm, "py": py},
        )

    async def index_tail(self, method: str, regions: list[str], per_region: int) -> list[Row]:
        return await self._q(
            """
            SELECT region_id, period, index_value FROM price_index
            WHERE method = {m:String} AND has({r:Array(String)}, region_id)
            ORDER BY region_id, period DESC LIMIT {n:UInt16} BY region_id""",
            {"m": method, "r": regions, "n": per_region},
        )


class BoundaryRepository:
    """시군구 경계 (V-World, 시각화용 단순화본) — 운영 DB."""

    def __init__(self, pg: AsyncConnectionPool):
        self.pg = pg

    async def meta(self) -> Row | None:
        async with self.pg.connection() as c:
            return cast(
                Row | None,
                await (
                    await c.execute(
                        """SELECT max(source_sha256) AS sha, max(fetched_at) AS at, count(*) AS n, max(source) AS src,
                              percentile_cont(0.5) WITHIN GROUP (ORDER BY area_rel_error) AS err_med,
                              max(area_rel_error) AS err_max
                       FROM ops.region_boundary"""
                    )
                ).fetchone(),
            )

    async def shapes(self) -> list[Row]:
        async with self.pg.connection() as c:
            return cast(
                list[Row],
                await (await c.execute("SELECT sgg_cd, geometry FROM ops.region_boundary ORDER BY sgg_cd")).fetchall(),
            )
