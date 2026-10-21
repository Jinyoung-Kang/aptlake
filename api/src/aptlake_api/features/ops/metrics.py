"""운영 지표 (FR-602): 파이프라인 신선도, 호출 예산 사용률, 파티션 상태, 품질 실패.
Prometheus 가 긁을 때마다 ops 스키마를 조회한다 (내부 리스너에서만 등록).

신선도 aptlake_freshness_seconds{kind}: fetch·publish = 매매(수집 원천 'rtms', 발행 기록 'trade'),
rent_fetch·rent_publish = 전월세 (ADR-050). 원천마다 따로 — 한쪽 발행이 멈춰도 다른 쪽 발행이 가리지 않게.
"""

from __future__ import annotations

import psycopg
from prometheus_client.core import GaugeMetricFamily

from ...core.settings import settings


class OpsCollector:
    def collect(self):
        try:
            with psycopg.connect(settings().pg_dsn.get_secret_value(), connect_timeout=3) as c:
                fresh = c.execute("""SELECT extract(epoch FROM now() - max(last_fetched_at)),
                                            extract(epoch FROM now() - (SELECT max(published_at)
                                                                        FROM ops.dataset_version
                                                                        WHERE source = 'trade'))
                                     FROM ops.ingest_partition WHERE source = 'rtms'""").fetchone()
                rent_fresh = c.execute("""SELECT extract(epoch FROM now() - max(last_fetched_at)),
                                                 extract(epoch FROM now() - (SELECT max(published_at)
                                                                             FROM ops.dataset_version
                                                                             WHERE source = 'rent'))
                                          FROM ops.ingest_partition WHERE source = 'rent'""").fetchone()
                status = c.execute(
                    "SELECT status, count(*) FROM ops.ingest_partition WHERE source = 'rtms' GROUP BY status"
                ).fetchall()
                budget = c.execute("""SELECT used_calls::float / nullif(limit_calls, 0) FROM ops.api_budget
                                      WHERE source='rtms' ORDER BY day DESC LIMIT 1""").fetchone()
                # 전월세 (ADR-050) — 기존 매매 지표 이름·라벨은 그대로 두고 따로 낸다 (대시보드 질의가 바뀌지 않게)
                rent_status = c.execute(
                    "SELECT status, count(*) FROM ops.ingest_partition WHERE source = 'rent' GROUP BY status"
                ).fetchall()
                rent_budget = c.execute("""SELECT used_calls::float / nullif(limit_calls, 0) FROM ops.api_budget
                                           WHERE source='rent' ORDER BY day DESC LIMIT 1""").fetchone()
                failed = c.execute("""SELECT count(*) FILTER (WHERE blocking), count(*) FROM ops.dq_result
                                      WHERE NOT passed AND at > now() - interval '24 hours'""").fetchone()
        except psycopg.Error:
            return
        if fresh is None or failed is None:
            return
        g = GaugeMetricFamily("aptlake_freshness_seconds", "seconds since last source fetch / publish", labels=["kind"])
        g.add_metric(["fetch"], float(fresh[0] or 0))
        g.add_metric(["publish"], float(fresh[1] or 0))
        # 전월세는 수집·발행 기록이 아직 없으면 싣지 않는다 (0 은 '방금'으로 읽힌다)
        for kind, v in zip(("rent_fetch", "rent_publish"), rent_fresh or (None, None), strict=True):
            if v is not None:
                g.add_metric([kind], float(v))
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
        rs = GaugeMetricFamily("aptlake_rent_partitions", "rent ingest partitions by status", labels=["status"])
        for st, n in rent_status:
            rs.add_metric([st], n)
        yield rs
        yield GaugeMetricFamily(
            "aptlake_rent_budget_used_ratio",
            "today's rent API call budget used / daily limit",
            value=float(rent_budget[0] or 0) if rent_budget else 0.0,
        )
        f = GaugeMetricFamily("aptlake_quality_failures_24h", "failed quality checks in 24h", labels=["blocking"])
        f.add_metric(["true"], failed[0])
        f.add_metric(["false"], failed[1] - failed[0])
        yield f
