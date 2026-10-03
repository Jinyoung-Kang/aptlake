"""품질·계보 데이터 접근 (운영 DB PostgreSQL, 파라미터 바인딩)."""

from __future__ import annotations

from typing import Any, cast

from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]
# 실패한 검사(d)가 뒤에 같은 자산·검사·파티션으로 통과했는가. 파티션 없음끼리도 같다고 본다.
# IS NOT DISTINCT FROM 은 색인을 못 써 실패 1건마다 표 전체를 읽었다(실데이터 7.9만 행, 57건에 446ms) →
# 파티션 있음·없음으로 나눠 (partition, at) 색인을 쓰게 한다 (8.6ms, 결과 동일)
RESOLVED = """CASE WHEN d.partition IS NULL THEN
         EXISTS (SELECT 1 FROM ops.dq_result x WHERE x.partition IS NULL AND x.asset = d.asset
                   AND x.check_name = d.check_name AND x.at > d.at AND x.passed)
       ELSE
         EXISTS (SELECT 1 FROM ops.dq_result x WHERE x.partition = d.partition AND x.asset = d.asset
                   AND x.check_name = d.check_name AND x.at > d.at AND x.passed)
       END"""


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
                    f"""SELECT d.asset, d.partition, d.check_name, d.severity, d.blocking, d.metric, d.at,
                              {RESOLVED} AS resolved
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
