"""품질·계보 데이터 접근 (운영 DB PostgreSQL, 파라미터 바인딩)."""

from __future__ import annotations

from typing import Any, cast

from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]


class QualityRepository:
    def __init__(self, pg: AsyncConnectionPool):
        self.pg = pg

    async def overview(self) -> dict[str, Any]:
        """요약 화면 한 번에 필요한 것 — 연결 하나로."""
        async with self.pg.connection() as c:
            status = await (
                await c.execute("SELECT status, count(*) AS n FROM ops.ingest_partition GROUP BY status")
            ).fetchall()
            fresh = await (
                await c.execute(
                    "SELECT max(last_fetched_at) AS fetched, max(last_changed_at) AS changed FROM ops.ingest_partition"
                )
            ).fetchone()
            ver = await (
                await c.execute(
                    """SELECT version, published_at, data_as_of FROM ops.dataset_version
                       ORDER BY published_at DESC LIMIT 1"""
                )
            ).fetchone()
            failed = await (
                await c.execute(
                    """SELECT d.asset, d.partition, d.check_name, d.severity, d.blocking, d.metric, d.at,
                              EXISTS (SELECT 1 FROM ops.dq_result x
                                      WHERE x.asset = d.asset AND x.partition IS NOT DISTINCT FROM d.partition
                                        AND x.check_name = d.check_name AND x.at > d.at AND x.passed) AS resolved
                       FROM ops.dq_result d
                       WHERE NOT d.passed AND d.at > now() - interval '7 days' ORDER BY d.at DESC LIMIT 200"""
                )
            ).fetchall()
            budget = await (
                await c.execute(
                    """SELECT day, limit_calls, used_calls, by_priority FROM ops.api_budget
                   WHERE source = 'rtms' ORDER BY day DESC LIMIT 7"""
                )
            ).fetchall()
        return cast(dict[str, Any], {"status": status, "fresh": fresh, "ver": ver, "failed": failed, "budget": budget})

    async def grid(self, ym_from: str, ym_to: str, sido: str | None) -> list[Row]:
        async with self.pg.connection() as c:
            return cast(
                list[Row],
                await (
                    await c.execute(
                        """SELECT p.sgg_cd, p.deal_ym, p.status, p.rows_last FROM ops.ingest_partition p
                       WHERE p.deal_ym BETWEEN %s AND %s AND (%s::text IS NULL OR left(p.sgg_cd, 2) = %s)
                       ORDER BY p.sgg_cd, p.deal_ym""",
                        (ym_from, ym_to, sido, sido),
                    )
                ).fetchall(),
            )

    async def rollup(self, ym_from: str, ym_to: str) -> list[Row]:
        async with self.pg.connection() as c:
            return cast(
                list[Row],
                await (
                    await c.execute(
                        """SELECT left(sgg_cd, 2) AS sido, deal_ym, status, count(*) AS n FROM ops.ingest_partition
                           WHERE deal_ym BETWEEN %s AND %s GROUP BY 1, 2, 3 ORDER BY 1, 2""",
                        (ym_from, ym_to),
                    )
                ).fetchall(),
            )

    async def partition(self, sgg: str, ym: str) -> dict[str, Any] | None:
        """파티션 상태 + 최신 검사 + 그 달을 발행한 마지막 데이터셋 버전 (없으면 None)."""
        async with self.pg.connection() as c:
            part = await (
                await c.execute(
                    """SELECT status, attempts, rows_last, rows_prev, payload_sha256, last_ingest_id, last_fetched_at,
                          last_changed_at, fetch_count, next_due_at, last_error
                   FROM ops.ingest_partition WHERE sgg_cd=%s AND deal_ym=%s""",
                    (sgg, ym),
                )
            ).fetchone()
            if part is None:
                return None
            checks = await (
                await c.execute(
                    """SELECT DISTINCT ON (asset, check_name) asset, check_name, passed, severity, blocking, metric, at
                   FROM ops.dq_result WHERE partition = %s OR partition = %s
                   ORDER BY asset, check_name, at DESC""",
                    (f"{sgg}/{ym}", ym),
                )
            ).fetchall()
            ver = await (
                await c.execute(
                    """SELECT version, published_at, snapshots FROM ops.dataset_version WHERE %s = ANY(partitions)
                   ORDER BY published_at DESC LIMIT 1""",
                    (ym,),
                )
            ).fetchone()
        return cast(dict[str, Any], {"part": part, "checks": checks, "ver": ver})
