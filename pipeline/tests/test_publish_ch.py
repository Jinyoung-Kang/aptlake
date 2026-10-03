"""월 발행의 서빙 표 교체 — 실제 ClickHouse(Testcontainers, 운영과 같은 스키마·서버 설정)로.

region_month 는 연 단위 파티션이라(ADR-037) 한 달을 발행해도 그 해 파티션을 통째로 바꾼다.
같은 해 다른 달·다른 해는 그대로여야 하고, 대조에 실패하면 서빙은 손대지 않는다.
trade_current 는 인덱스 입도 1024 (ADR-038) — 기존 8192 표는 이관 스크립트가 행을 그대로 옮겨 바꾼다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import clickhouse_connect
import pytest
from testcontainers.community.clickhouse import ClickHouseContainer

from aptlake_pipeline import publish

ROOT = Path(__file__).resolve().parents[2]
PW = "test-only-password"
COLS = ["sgg_cd", "month", "reported", "trades", "cancelled", "priced", "outliers",
        "p25_ppm2", "median_ppm2", "p75_ppm2", "low_sample", "dataset_ver"]  # fmt: skip
CHECKS = {"n": "count()", "trades": "sum(trades)", "cancelled": "sum(cancelled)"}


def _statements(path: Path) -> list[str]:
    out = []
    for chunk in path.read_text().split(";"):
        body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


@pytest.fixture(scope="module")
def ch():
    with ClickHouseContainer("clickhouse/clickhouse-server:26.8", username="admin", password=PW).with_volume_mapping(
        str(ROOT / "infra/clickhouse/config.d/aptlake.xml"), "/etc/clickhouse-server/config.d/aptlake.xml"
    ) as c:
        host, port = c.get_container_host_ip(), int(c.get_exposed_port(8123))
        admin = clickhouse_connect.get_client(host=host, port=port, username="admin", password=PW)
        for stmt in _statements(ROOT / "infra/clickhouse/init/01-schema.sql"):
            admin.command(stmt)
        yield clickhouse_connect.get_client(host=host, port=port, username="admin", password=PW, database="aptlake")


def row(sgg: str, month: dt.date, trades: int, ver: str) -> list:
    return [sgg, month, trades + 1, trades, 1, trades, 0, 900.0, 1000.0 + trades, 1100.0, 0, ver]


def seed(ch, rows: list[list]) -> None:
    ch.command("TRUNCATE TABLE region_month")
    ch.command("TRUNCATE TABLE region_month_staging")
    ch.insert("region_month", rows, column_names=COLS)


def serving(ch) -> dict[tuple[str, str], tuple[int, str]]:
    return {
        (r[0], f"{r[1]:%Y-%m}"): (r[2], r[3])
        for r in ch.query("SELECT sgg_cd, month, trades, dataset_ver FROM region_month").result_rows
    }


def publish_region_month(ch, ym: str, rows: list[list], expected: dict | None = None) -> None:
    exp = expected or {"n": len(rows), "trades": sum(r[3] for r in rows), "cancelled": sum(r[4] for r in rows)}
    try:
        n = publish._stage(ch, "region_month", ym, rows, COLS, CHECKS, exp)
    except Exception:
        publish._drop_staged(ch, ["region_month"], ym)
        raise
    publish._swap(ch, "region_month", ym, n)


D = dt.date
BASE = [row("11110", D(2023, 12, 1), 5, "v1"),
        row("11110", D(2024, 1, 1), 10, "v1"), row("41135", D(2024, 1, 1), 20, "v1"),
        row("11110", D(2024, 2, 1), 30, "v1"), row("41135", D(2024, 2, 1), 40, "v1"),
        row("11110", D(2024, 3, 1), 50, "v1")]  # fmt: skip


def test_schema_partitions_region_month_by_year(ch):
    keys = dict(ch.query(
        "SELECT name, partition_key FROM system.tables WHERE database = 'aptlake' AND name LIKE 'region_month%'"
    ).result_rows)  # fmt: skip
    assert keys == {"region_month": "toYear(month)", "region_month_staging": "toYear(month)"}


def test_publishing_a_month_replaces_only_that_month_of_the_year(ch):
    seed(ch, BASE)
    publish_region_month(ch, "202402", [row("11110", D(2024, 2, 1), 31, "v2"), row("26110", D(2024, 2, 1), 7, "v2")])
    assert serving(ch) == {
        ("11110", "2023-12"): (5, "v1"),  # 다른 해 그대로
        ("11110", "2024-01"): (10, "v1"), ("41135", "2024-01"): (20, "v1"),  # 같은 해 다른 달은 서빙 행 그대로
        ("11110", "2024-02"): (31, "v2"), ("26110", "2024-02"): (7, "v2"),  # 발행한 달만 새 값 (41135 는 빠짐)
        ("11110", "2024-03"): (50, "v1"),
    }  # fmt: skip
    assert ch.query("SELECT count() FROM region_month_staging").first_row[0] == 0
    parts = ch.query(
        "SELECT DISTINCT partition FROM system.parts WHERE database = 'aptlake' AND table = 'region_month' AND active"
    ).result_rows
    assert sorted(p[0] for p in parts) == ["2023", "2024"]


def test_month_without_rows_removes_only_that_month(ch):
    seed(ch, BASE)
    publish_region_month(ch, "202403", [])
    assert ("11110", "2024-03") not in serving(ch) and ("11110", "2024-01") in serving(ch)
    publish_region_month(ch, "202312", [])  # 그 해에 남는 달이 없으면 파티션째 지운다
    assert all(not m.startswith("2023") for _, m in serving(ch))


def test_failed_reconciliation_leaves_serving_untouched(ch):
    seed(ch, BASE)
    before = serving(ch)
    with pytest.raises(publish.ReconciliationError):
        publish_region_month(
            ch, "202402", [row("11110", D(2024, 2, 1), 99, "v2")], {"n": 2, "trades": 99, "cancelled": 1}
        )
    assert serving(ch) == before
    assert ch.query("SELECT count() FROM region_month_staging").first_row[0] == 0


def test_publish_does_not_swap_from_staging_recreated_after_reconciliation(ch, monkeypatch):
    """대조를 통과한 뒤 스테이징이 빈 표로 다시 만들어지면(예: 수집 중 서빙 표 이관) 교체하지 않는다.
    그대로 교체하면 빈 스테이징으로 REPLACE·DROP PARTITION 이 되어 서빙에서 그 해·그 달이 지워지고 발행은 성공으로 남는다."""
    seed(ch, BASE)
    before = serving(ch)

    def stage_then_staging_recreated(client, ym, ver, counts):
        rows = [row("11110", D(2024, 2, 1), 31, "v2")]
        counts["region_month"] = publish._stage(client, "region_month", ym, rows, COLS, CHECKS,
                                                {"n": 1, "trades": 31, "cancelled": 1})  # fmt: skip
        for t in ("rollup_month", "trade_current", "trade_version"):
            counts[t] = 0
        client.command("DROP TABLE region_month_staging")
        client.command("CREATE TABLE region_month_staging AS region_month")

    monkeypatch.setattr(publish, "ch", lambda: ch)
    monkeypatch.setattr(publish, "_stage_month", stage_then_staging_recreated)
    with pytest.raises(publish.ReconciliationError):
        publish.publish_month("202402", "v2")
    assert serving(ch) == before


def test_migration_converts_monthly_table_without_changing_rows(ch):
    """운영에 이미 있는 월 파티션 표 → 연 파티션 (ch-migrate 가 한 번 실행하는 스크립트)."""
    ch.command("DROP TABLE region_month")
    ch.command("DROP TABLE region_month_staging")
    ch.command(f"CREATE TABLE aptlake.region_month {_columns('region_month')} ENGINE = MergeTree "
               "PARTITION BY toYYYYMM(month) ORDER BY (sgg_cd, month)")  # fmt: skip
    ch.command("CREATE TABLE region_month_staging AS region_month")
    ch.insert("region_month", BASE, column_names=COLS)
    before = ch.query("SELECT count(), sum(trades), sum(cancelled), sum(priced) FROM region_month").first_row
    for stmt in _statements(ROOT / "infra/clickhouse/migrate/region_month_yearly.sql"):
        ch.command(stmt)
    test_schema_partitions_region_month_by_year(ch)
    after = ch.query("SELECT count(), sum(trades), sum(cancelled), sum(priced) FROM region_month").first_row
    assert after == before
    assert ch.query("EXISTS TABLE region_month_yearly_new").first_row[0] == 0


GRANULARITY = """SELECT name, extract(engine_full, 'index_granularity = ([0-9]+)') FROM system.tables
                 WHERE database = 'aptlake' AND name IN ('trade_current', 'trade_current_staging')"""
CHECKSUM = "SELECT count(), sum(cityHash64(toString(tuple(*)))) FROM trade_current"


def test_schema_sets_trade_current_granularity(ch):
    assert dict(ch.query(GRANULARITY).result_rows) == {"trade_current": "1024", "trade_current_staging": "1024"}


def test_granularity_migration_keeps_rows_index_and_staging(ch):
    """운영에 이미 있는 8192 표 → 1024 (ch-migrate 가 한 번 실행). 스테이징도 같은 입도로 다시 만든다."""
    ch.command("DROP TABLE trade_current")
    ch.command("DROP TABLE trade_current_staging")
    ch.command(f"CREATE TABLE aptlake.trade_current {_columns('trade_current')} ENGINE = MergeTree "
               "PARTITION BY toYYYYMM(deal_date) ORDER BY (sgg_cd, deal_date, trade_id)")  # fmt: skip
    ch.command("CREATE TABLE trade_current_staging AS trade_current")
    ch.command("""INSERT INTO trade_current
        SELECT concat('t', toString(number)), if(number % 2, '11110', '41135'), toDate('2024-01-01') + number % 60,
               concat('c', toString(number % 37)), '아파트', '동', '1', 84.5, if(number % 7 = 0, NULL, number % 30),
               50000 + number, 600.5, number % 11 = 0, if(number % 11 = 0, toDate('2024-03-01'), NULL), NULL,
               '', '', '', '', 2000, 0, 1, now64(3), NULL
        FROM numbers(20000)""")
    assert dict(ch.query(GRANULARITY).result_rows) == {"trade_current": "8192", "trade_current_staging": "8192"}
    before = ch.query(CHECKSUM).first_row
    for stmt in _statements(ROOT / "infra/clickhouse/migrate/trade_current_granularity.sql"):
        ch.command(stmt)
    test_schema_sets_trade_current_granularity(ch)
    assert ch.query(CHECKSUM).first_row == before
    assert ch.query("EXISTS TABLE trade_current_g1024_new").first_row[0] == 0
    skip = ch.query("SELECT table, name, type, granularity FROM system.data_skipping_indices "
                    "WHERE database = 'aptlake' AND table LIKE 'trade_current%'").result_rows  # fmt: skip
    assert sorted(skip) == [("trade_current", "idx_complex", "bloom_filter", 4),
                            ("trade_current_staging", "idx_complex", "bloom_filter", 4)]  # fmt: skip
    # 파트도 새 입도로 쓰였는가: 2만 행이 1024행 그래뉼이면 마크 약 20개 (8192 면 3~4개)
    marks = ch.query("SELECT sum(marks) FROM system.parts WHERE database = 'aptlake' "
                     "AND table = 'trade_current' AND active").first_row[0]  # fmt: skip
    assert marks >= 20000 // 1024


def _columns(table: str) -> str:
    """이관 전 운영 표를 만들 때 쓰는 열 정의 — 스키마 파일의 것을 그대로 쓴다."""
    ddl = (ROOT / "infra/clickhouse/init/01-schema.sql").read_text()
    start = ddl.index(f"CREATE TABLE IF NOT EXISTS aptlake.{table}\n")
    body = "\n".join(
        line for line in ddl[start : ddl.index(";", start)].splitlines() if not line.strip().startswith("--")
    )
    return body[body.index("(") : body.index(") ENGINE") + 1]
