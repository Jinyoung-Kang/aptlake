"""지수 데이터 접근 (ClickHouse, 서버측 바인딩)."""

from __future__ import annotations

from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

Row = dict[str, Any]


class IndexRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any] | None = None) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params or {})).named_results())

    async def series(self, region: str, method: str) -> list[Row]:
        return await self._q(
            """SELECT period, index_value, ci_low, ci_high, n_obs, model_ver FROM price_index
               WHERE region_id = {r:String} AND method = {m:String} ORDER BY period""",
            {"r": region, "m": method},
        )

    async def reference(self, region: str) -> list[Row]:
        return await self._q(
            """SELECT period, value, source FROM index_reference
               WHERE region_id = {r:String} ORDER BY period""",
            {"r": region},
        )

    async def validation(self, region: str, method: str) -> Row | None:
        rows = await self._q(
            """SELECT reference, corr_mom, direction_match, n_months, window_from, window_to
               FROM index_validation WHERE region_id = {r:String} AND method = {m:String}""",
            {"r": region, "m": method},
        )
        return rows[0] if rows else None

    async def all_series(self, method: str) -> list[Row]:
        """지역별 (기간, 값) 목록 — 기간 순."""
        return await self._q(
            """
            SELECT region_id, arraySort(x -> x.1, groupArray((period, index_value))) AS pts
            FROM price_index WHERE method = {m:String}
            GROUP BY region_id""",
            {"m": method},
        )

    async def all_validations(self, method: str) -> list[Row]:
        return await self._q(
            """
            SELECT region_id, corr_mom, direction_match, n_months FROM index_validation
            WHERE method = {m:String}""",
            {"m": method},
        )
