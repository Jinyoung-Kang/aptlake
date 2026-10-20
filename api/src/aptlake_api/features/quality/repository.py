"""품질·계보 데이터 접근 (운영 DB PostgreSQL, 파라미터 바인딩)."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg_pool import AsyncConnectionPool

from .service import L3_BUCKETS

Row = dict[str, Any]
# 전월세 품질 기록의 자산 (ADR-050) — 매매 화면에서는 빼고 전월세 화면에서는 이것만
RENT_ASSETS = ["bronze.rtms_rent_raw", "silver.apt_rent"]
DATASET_SOURCE = {"rtms": "trade", "rent": "rent"}  # 수집 원천 → 발행 기록의 원천
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

    async def overview(self, source: str = "rtms") -> dict[str, Any]:
        """요약 화면 한 번에 필요한 것 — 연결 하나로."""
        async with self.pg.connection() as c:
            status = await (
                await c.execute(
                    "SELECT status, count(*) AS n FROM ops.ingest_partition WHERE source = %s GROUP BY status",
                    (source,),
                )
            ).fetchall()
            schema = await (
                await c.execute(
                    """SELECT schema_ver, count(*) AS n FROM ops.ingest_partition
                       WHERE source = %s AND status IN ('LOADED', 'MERGED') GROUP BY schema_ver ORDER BY schema_ver""",
                    (source,),
                )
            ).fetchall()
            fresh = await (
                await c.execute(
                    """SELECT max(last_fetched_at) AS fetched, max(last_changed_at) AS changed
                       FROM ops.ingest_partition WHERE source = %s""",
                    (source,),
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
                       WHERE NOT d.passed AND d.at > now() - interval '7 days'
                         AND (d.asset = ANY(%s)) = %s
                       ORDER BY d.at DESC LIMIT 200""",
                    (RENT_ASSETS, source == "rent"),
                )
            ).fetchall()
            budget = await (
                await c.execute(
                    """SELECT day, limit_calls, used_calls, by_priority FROM ops.api_budget
                   WHERE source = %s ORDER BY day DESC LIMIT 7""",
                    (source,),
                )
            ).fetchall()
        return cast(
            dict[str, Any],
            {"status": status, "schema": schema, "fresh": fresh, "ver": ver, "failed": failed, "budget": budget},
        )

    async def grid(self, ym_from: str, ym_to: str, sido: str | None, source: str = "rtms") -> list[Row]:
        async with self.pg.connection() as c:
            return cast(
                list[Row],
                await (
                    await c.execute(
                        """SELECT p.sgg_cd, p.deal_ym, p.status, p.rows_last FROM ops.ingest_partition p
                       WHERE p.source = %s AND p.deal_ym BETWEEN %s AND %s
                         AND (%s::text IS NULL OR left(p.sgg_cd, 2) = %s)
                       ORDER BY p.sgg_cd, p.deal_ym""",
                        (source, ym_from, ym_to, sido, sido),
                    )
                ).fetchall(),
            )

    async def rollup(self, ym_from: str, ym_to: str, source: str = "rtms") -> list[Row]:
        async with self.pg.connection() as c:
            return cast(
                list[Row],
                await (
                    await c.execute(
                        """SELECT left(sgg_cd, 2) AS sido, deal_ym, status, count(*) AS n FROM ops.ingest_partition
                           WHERE source = %s AND deal_ym BETWEEN %s AND %s GROUP BY 1, 2, 3 ORDER BY 1, 2""",
                        (source, ym_from, ym_to),
                    )
                ).fetchall(),
            )

    async def partition(self, sgg: str, ym: str, source: str = "rtms") -> dict[str, Any] | None:
        """파티션 상태 + 최신 검사 + 그 달을 발행한 마지막 데이터셋 버전 (없으면 None)."""
        async with self.pg.connection() as c:
            part = await (
                await c.execute(
                    """SELECT status, attempts, rows_last, rows_prev, payload_sha256, last_ingest_id, last_fetched_at,
                          last_changed_at, fetch_count, next_due_at, last_error
                   FROM ops.ingest_partition WHERE source = %s AND sgg_cd=%s AND deal_ym=%s""",
                    (source, sgg, ym),
                )
            ).fetchone()
            if part is None:
                return None
            checks = await (
                await c.execute(
                    """SELECT DISTINCT ON (asset, check_name) asset, check_name, passed, severity, blocking, metric, at
                   FROM ops.dq_result WHERE (partition = %s OR partition = %s) AND (asset = ANY(%s)) = %s
                   ORDER BY asset, check_name, at DESC""",
                    (f"{sgg}/{ym}", ym, RENT_ASSETS, source == "rent"),
                )
            ).fetchall()
            ver = await (
                await c.execute(
                    """SELECT version, published_at, snapshots FROM ops.dataset_version
                   WHERE source = %s AND %s = ANY(partitions) ORDER BY published_at DESC LIMIT 1""",
                    (DATASET_SOURCE[source], ym),
                )
            ).fetchone()
        return cast(dict[str, Any], {"part": part, "checks": checks, "ver": ver})


class LatencyRepository:
    """원천 반영 지연: L1·L2 분포(ClickHouse source_latency, 매일 교체) + L3 발행 기록(PostgreSQL ops.publish_lag)."""

    def __init__(self, pg: AsyncConnectionPool, ch: AsyncClient):
        self.pg, self.ch = pg, ch

    async def source_latency(self, start: dt.date, end: dt.date, sido: str | None) -> list[Row]:
        where = "observed_date BETWEEN {a:Date} AND {b:Date}" + (" AND sido_cd = {s:String}" if sido else "")
        res = await self.ch.query(
            f"""SELECT metric, reason, lag_days, sum(n) AS n FROM source_latency WHERE {where}
                GROUP BY metric, reason, lag_days ORDER BY metric, reason, lag_days""",
            parameters={"a": start, "b": end, "s": sido or ""},
        )
        return list(res.named_results())

    async def latency_since(self) -> dict[str, dt.date | None]:
        res = await self.ch.query(
            "SELECT metric, min(observed_date) FROM source_latency WHERE reason = '' GROUP BY metric"
        )
        return {r[0]: r[1] for r in res.result_rows}

    async def publish_lag(self, start: dt.date, end: dt.date, sido: str | None) -> Row:
        buckets = ", ".join(
            f"count(*) FILTER (WHERE m > {lo} AND m <= {hi}) AS b{i}"
            if hi is not None
            else f"count(*) FILTER (WHERE m > {lo}) AS b{i}"
            for i, (lo, hi) in enumerate(zip([-1, *L3_BUCKETS[:-1]], L3_BUCKETS, strict=True))
        )
        async with self.pg.connection() as c:
            cur = await c.execute(
                f"""WITH l AS (
                        SELECT extract(epoch FROM published_at - fetched_at) / 60 AS m FROM ops.publish_lag
                        WHERE (published_at AT TIME ZONE 'Asia/Seoul')::date BETWEEN %(a)s AND %(b)s
                          AND (%(s)s::text IS NULL OR left(sgg_cd, 2) = %(s)s))
                    SELECT count(*) AS n,
                           round(percentile_disc(0.5) WITHIN GROUP (ORDER BY m)) AS p50,
                           round(percentile_disc(0.9) WITHIN GROUP (ORDER BY m)) AS p90,
                           round(percentile_disc(0.99) WITHIN GROUP (ORDER BY m)) AS p99,
                           {buckets},
                           (SELECT min(published_at AT TIME ZONE 'Asia/Seoul')::date FROM ops.publish_lag) AS since
                    FROM l""",
                {"a": start, "b": end, "s": sido},
            )
            return cast(Row, await cur.fetchone())
