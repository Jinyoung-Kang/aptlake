"""수집 상태·오류 로그 데이터 접근. 운영 메타데이터는 운영 DB(ops 스키마), API 오류는 ClickHouse usage_event."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from clickhouse_connect.driver import exceptions as ch_exc
from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from ...core.errors import ch_transient
from ..quality.repository import RESOLVED
from .service import SourceUnavailable

Row = dict[str, Any]


class OpsRepository:
    def __init__(self, pg: AsyncConnectionPool, ch: AsyncClient, redis: Redis):
        self.pg, self.ch, self.redis = pg, ch, redis

    async def _all(self, sql: str, params: tuple = ()) -> list[Row]:
        async with self.pg.connection() as c:
            return cast(list[Row], await (await c.execute(sql, params)).fetchall())

    async def _one(self, sql: str, params: tuple = ()) -> Row | None:
        rows = await self._all(sql, params)
        return rows[0] if rows else None

    async def _ch(self, sql: str, params: dict[str, Any]) -> list[Row]:
        """서빙 DB 일시 장애는 SourceUnavailable 로 (화면의 나머지는 보여 준다), 버그(문법·권한)는 그대로 올린다."""
        try:
            return list((await self.ch.query(sql, parameters=params)).named_results())
        except ch_exc.Error as e:
            reason = ch_transient(e)
            if not reason:
                raise
            raise SourceUnavailable(reason) from e

    # ── 수집 상태 ──
    async def budget_day(self, day: dt.date) -> Row | None:
        return await self._one(
            """SELECT day, limit_calls, used_calls, by_priority, exhausted_reason, exhausted_at
               FROM ops.api_budget WHERE source='rtms' AND day=%s""",
            (day,),
        )

    async def partition_counts(self) -> dict[str, int]:
        rows = await self._all("SELECT status, count(*) AS n FROM ops.ingest_partition GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    async def month_counts(self) -> Row | None:
        return await self._one(
            """SELECT count(*) FILTER (WHERE needs_publish) AS pending,
                      count(*) FILTER (WHERE published_at IS NOT NULL) AS published
               FROM ops.month_state"""
        )

    async def last_version(self) -> Row | None:
        return await self._one(
            "SELECT version, published_at FROM ops.dataset_version ORDER BY published_at DESC LIMIT 1"
        )

    # ── 오류 로그 ──
    async def log_view(self) -> Row | None:
        return await self._one("SELECT cleared_at, cleared_by FROM ops.log_view WHERE view_id = 'errors'")

    async def set_errors_cleared(self, clear: bool, actor: str) -> dt.datetime | None:
        async with self.pg.connection() as c, c.transaction():
            row = await (
                await c.execute(
                    """UPDATE ops.log_view SET cleared_at = CASE WHEN %s THEN now() END,
                              cleared_by = CASE WHEN %s THEN %s END, updated_at = now()
                       WHERE view_id = 'errors' RETURNING cleared_at""",
                    (clear, clear, actor),
                )
            ).fetchone()
            await c.execute(
                """INSERT INTO api.audit_log (actor, action, target, detail) VALUES (%s, %s, 'ops.errors', '{}')""",
                (actor, "ops.errors.clear" if clear else "ops.errors.restore"),
            )
        return cast(Row, row)["cleared_at"] if row else None

    async def ingest_errors(self, since: dt.datetime) -> list[Row]:
        return await self._all(
            """
            SELECT deal_ym, status, last_error, count(*) AS n, max(updated_at) AS at,
                   (array_agg(sgg_cd ORDER BY sgg_cd))[1:8] AS sample
            FROM ops.ingest_partition
            WHERE status IN ('RETRY','QUARANTINED') AND last_error IS NOT NULL AND updated_at >= %s
            GROUP BY deal_ym, status, last_error ORDER BY at DESC LIMIT 200""",
            (since,),
        )

    async def budget_exhaustions(self, since: dt.datetime) -> list[Row]:
        return await self._all(
            """SELECT day, exhausted_reason, exhausted_at FROM ops.api_budget
               WHERE source='rtms' AND exhausted_at >= %s""",
            (since,),
        )

    async def quality_failures(self, since: dt.datetime) -> list[Row]:
        return await self._all(
            f"""
            SELECT d.check_id, d.asset, d.partition, d.check_name, d.severity, d.blocking, d.metric, d.at,
                   {RESOLVED} AS resolved
            FROM ops.dq_result d
            WHERE NOT d.passed AND d.at >= %s ORDER BY d.at DESC LIMIT 400""",
            (since,),
        )

    async def api_5xx(self, since: dt.datetime) -> list[Row]:
        return await self._ch(
            """SELECT at, route, status, latency_ms, trace_id, error FROM usage_event
               WHERE status >= 500 AND at >= {since:DateTime64(3, 'UTC')} ORDER BY at DESC LIMIT 100""",
            {"since": since},
        )

    async def api_error_summary(self, since: dt.datetime) -> list[Row]:
        return await self._ch(
            """SELECT route, status, count() AS n FROM usage_event
               WHERE status >= 400 AND at >= {since:DateTime64(3, 'UTC')}
               GROUP BY route, status ORDER BY n DESC LIMIT 20""",
            {"since": since},
        )

    # ── 연결 점검 ──
    async def last_fetched(self) -> dt.datetime | None:
        row = await self._one("SELECT max(last_fetched_at) AS at FROM ops.ingest_partition")
        return row["at"] if row else None

    async def boundary_fetched(self) -> dt.datetime | None:
        row = await self._one("SELECT max(fetched_at) AS at FROM ops.region_boundary")
        return row["at"] if row else None

    async def ping_pg(self) -> None:
        async with self.pg.connection() as c:
            await c.execute("SELECT 1")

    async def cached_dataset_version(self) -> str | None:
        await self.redis.ping()
        return cast(str | None, await self.redis.get("al:ds:ver"))  # decode_responses=True

    async def serving_health(self) -> tuple[dt.date | None, float | None, float | None]:
        """최근 계약월, 서버 메모리 사용량·상한(바이트)."""
        r = await self.ch.query(
            """SELECT (SELECT max(month) FROM region_month),
                      (SELECT value FROM system.metrics WHERE metric = 'MemoryTracking'),
                      (SELECT toUInt64(value) FROM system.server_settings WHERE name = 'max_server_memory_usage')"""
        )
        return cast(tuple, r.result_rows[0]) if r.result_rows else (None, None, None)
