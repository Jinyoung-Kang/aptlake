"""사용량 데이터 접근 (ClickHouse usage_event, 서버측 바인딩)."""

from __future__ import annotations

from typing import Any

from clickhouse_connect.driver.asyncclient import AsyncClient

Row = dict[str, Any]


class UsageRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def daily(self, client_id: str, days: int) -> list[Row]:
        res = await self.ch.query(
            """
            SELECT toDate(at, 'Asia/Seoul') AS day, count() AS requests, sum(rows) AS rows,
                   countIf(status >= 400) AS errors, quantile(0.95)(latency_ms) AS p95_ms
            FROM usage_event
            WHERE client_id = {c:String} AND at >= now() - toIntervalDay({d:UInt16})
            GROUP BY day ORDER BY day""",
            parameters={"c": client_id, "d": days},
        )
        return list(res.named_results())
