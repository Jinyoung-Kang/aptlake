"""운영 지표 (FR-602): 파이프라인 신선도, 호출 예산 사용률, 파티션 상태, 품질 실패.
Prometheus 가 긁을 때마다 ops 스키마를 조회한다 (내부 리스너에서만 등록)."""

from __future__ import annotations

import psycopg
from prometheus_client.core import GaugeMetricFamily

from .settings import settings


class OpsCollector:
    def collect(self):
        try:
            with psycopg.connect(settings().pg_dsn.get_secret_value(), connect_timeout=3) as c:
                fresh = c.execute("""SELECT extract(epoch FROM now() - max(last_fetched_at)),
                                            extract(epoch FROM now() - (SELECT max(published_at)
                                                                        FROM ops.dataset_version))
                                     FROM ops.ingest_partition""").fetchone()
                status = c.execute("SELECT status, count(*) FROM ops.ingest_partition GROUP BY status").fetchall()
                budget = c.execute("""SELECT used_calls::float / nullif(limit_calls, 0) FROM ops.api_budget
                                      WHERE source='rtms' ORDER BY day DESC LIMIT 1""").fetchone()
                failed = c.execute("""SELECT count(*) FILTER (WHERE blocking), count(*) FROM ops.dq_result
                                      WHERE NOT passed AND at > now() - interval '24 hours'""").fetchone()
        except psycopg.Error:
            return
        if fresh is None or failed is None:
            return
        g = GaugeMetricFamily("aptlake_freshness_seconds", "seconds since last source fetch / publish", labels=["kind"])
        g.add_metric(["fetch"], float(fresh[0] or 0))
        g.add_metric(["publish"], float(fresh[1] or 0))
        yield g
        s = GaugeMetricFamily("aptlake_partitions", "ingest partitions by status", labels=["status"])
        for st, n in status:
            s.add_metric([st], n)
        yield s
        yield GaugeMetricFamily(
            "aptlake_budget_used_ratio",
            "today's RTMS call budget used / daily limit",
            value=float(budget[0] or 0) if budget else 0.0,
        )
        f = GaugeMetricFamily("aptlake_quality_failures_24h", "failed quality checks in 24h", labels=["blocking"])
        f.add_metric(["true"], failed[0])
        f.add_metric(["false"], failed[1] - failed[0])
        yield f
