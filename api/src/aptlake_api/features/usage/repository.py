"""사용량 데이터 접근 (ClickHouse usage_event, 서버측 바인딩)."""

from __future__ import annotations

from typing import Any, cast

from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]


class UsageRepository:
    def __init__(self, ch: AsyncClient, pg: AsyncConnectionPool | None = None):
        self.ch, self.pg = ch, pg

    async def monthly(self, client_id: str, months: int) -> list[Row]:
        res = await self.ch.query(
            """
            SELECT formatDateTime(toStartOfMonth(toDate(at, 'Asia/Seoul')), '%Y-%m') AS month, count() AS requests,
                   countIf(status >= 400) AS errors, countIf(status = 429) AS rate_limited, sum(rows) AS rows
            FROM usage_event
            WHERE client_id = {c:String}
              AND toDate(at, 'Asia/Seoul') >= addMonths(toStartOfMonth(toDate(now(), 'Asia/Seoul')), -{m:UInt8} + 1)
            GROUP BY month""",
            parameters={"c": client_id, "m": months},
        )
        return list(res.named_results())

    async def export_monthly(self, client_id: str, months: int) -> list[Row]:
        assert self.pg is not None
        async with self.pg.connection() as c:
            cur = await c.execute(
                """SELECT to_char(finished_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM') AS month, count(*) AS jobs,
                          coalesce(sum(rows), 0) AS rows
                   FROM api.export_job
                   WHERE client_id = %s AND status = 'done'
                     AND finished_at >= date_trunc('month', now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'
                                        - make_interval(months => %s - 1)
                   GROUP BY month""",
                (client_id, months),
            )
            return cast(list[Row], await cur.fetchall())

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
