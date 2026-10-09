"""발행 기록 SQL — 실제 PostgreSQL(운영과 같은 마이그레이션, pipeline 역할 권한)로.

달 발행 때 버전·달 스냅숏 파일·수집→발행 지연(L3)이 한 트랜잭션으로 남고(ADR-044·046),
지연은 직전 기록 발행 이후 새로 수집된 MERGED 파티션만 센다.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row
from testcontainers.community.postgres import PostgresContainer

from aptlake_pipeline import ops_db
from aptlake_pipeline.snapshots import MonthFile

ROOT = Path(__file__).resolve().parents[2]
PW = "test-only-password"


@pytest.fixture(scope="module")
def pipeline_dsn():
    with PostgresContainer("postgres:16-alpine", username="postgres", password=PW, dbname="aptlake") as pg:
        su = f"postgresql://postgres:{PW}@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/aptlake"
        with psycopg.connect(su, autocommit=True) as c:
            for role in ("pipeline", "api_app", "migrator"):
                c.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{PW}'")
            for f in sorted((ROOT / "api/migrations").glob("V*.sql")):
                c.execute(f.read_text())
            c.execute(
                """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, last_changed_at) VALUES
                   ('11110', '202609', 'MERGED', now() - interval '1 day'),
                   ('11140', '202609', 'MERGED', now() - interval '1 day'),
                   ('11170', '202609', 'LOADED', now() - interval '1 day')"""
            )
        yield su.replace(f"postgres:{PW}", f"pipeline:{PW}"), su


@pytest.fixture
def use_pg(monkeypatch, pipeline_dsn):
    dsn, _ = pipeline_dsn

    @contextlib.contextmanager
    def conn():
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            yield c

    monkeypatch.setattr(ops_db, "conn", conn)
    return pipeline_dsn[1]


def mf(ym: str, pid: str) -> MonthFile:
    return MonthFile(ym, f"trade/deal_ym={ym}/{pid}.parquet", 10, 100, "a" * 64)


def test_month_publish_records_files_and_lag_only_for_newly_collected_partitions(use_pg):
    base, p2 = "f0000000-0000-0000-0000-000000000001", "f0000000-0000-0000-0000-000000000002"
    ops_db.record_dataset_version("gold@t.1", ops_db_now(use_pg), ["202609"], {}, {}, None, base, [mf("202609", base)],
                                  baseline=True)  # fmt: skip
    with psycopg.connect(use_pg, autocommit=True) as c:  # 기준선 뒤 11110 만 새로 수집 (11170 은 검사에 막혀 LOADED)
        c.execute(
            "UPDATE ops.ingest_partition SET last_changed_at = clock_timestamp() WHERE sgg_cd IN ('11110','11170')"
        )
    assert ops_db.last_month_file("202609") == mf("202609", base).object_key
    ops_db.record_dataset_version("gold@t.2", ops_db_now(use_pg), ["202609"], {}, {}, None, p2, [mf("202609", p2)])
    with psycopg.connect(use_pg) as c:
        lag = c.execute("SELECT sgg_cd, published_at > fetched_at FROM ops.publish_lag WHERE publish_id = %s", (p2,))
        assert lag.fetchall() == [("11110", True)]
        assert c.execute("SELECT count(*) FROM ops.publish_lag WHERE publish_id = %s", (base,)).fetchone() == (0,)
    assert ops_db.last_month_file("202609") == mf("202609", p2).object_key


def test_record_is_all_or_nothing(use_pg):
    pid = "f0000000-0000-0000-0000-000000000003"
    with pytest.raises(psycopg.errors.UniqueViolation):  # 같은 달 파일 두 번 → 버전 기록도 남지 않는다
        ops_db.record_dataset_version("gold@t.3", ops_db_now(use_pg), ["202609"], {}, {}, None, pid,
                                      [mf("202609", pid), mf("202609", pid)])  # fmt: skip
    with psycopg.connect(use_pg) as c:
        assert c.execute("SELECT count(*) FROM ops.dataset_version WHERE version = 'gold@t.3'").fetchone() == (0,)


def ops_db_now(su: str):
    with psycopg.connect(su) as c:
        return c.execute("SELECT now()").fetchone()[0]
