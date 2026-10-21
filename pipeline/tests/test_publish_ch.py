"""월 발행의 서빙 표 교체 — 실제 ClickHouse(Testcontainers, 운영과 같은 스키마·서버 설정)로.

region_month 는 연 단위 파티션이라(ADR-037) 한 달을 발행해도 그 해 파티션을 통째로 바꾼다.
같은 해 다른 달·다른 해는 그대로여야 하고, 대조에 실패하면 서빙은 손대지 않는다.
trade_current 는 인덱스 입도 1024 (ADR-038) — 기존 8192 표는 이관 스크립트가 행을 그대로 옮겨 바꾼다.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import clickhouse_connect
import pyarrow.parquet as pq
import pytest
from testcontainers.community.clickhouse import ClickHouseContainer

from aptlake_pipeline import publish, snapshots

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
        publish.publish_month("202402", "v2", "pid", FakeStore())
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


# ───── 달별 스냅숏 파일 (ADR-044) ─────
class FakeStore:
    """snapshots 버킷 대신 — 올린 파일을 표로 읽어 둔다."""

    def __init__(self, fail: bool = False):
        self.fail, self.files = fail, {}

    def put(self, path, key):
        if self.fail:
            raise OSError("s3 down")
        self.files[key] = pq.read_table(path)

    def read(self, key):
        return self.files[key]


def trade(tid: str, day: dt.date, price: int) -> list:
    return [tid, "11110", day, "c1", "단지", "숭인동", "1", Decimal("84.9900"), 5, price, price / 84.99, 0, None, None,
            "", "중개거래", "개인", "개인", 2000, 0, 1, dt.datetime(2026, 9, 2, tzinfo=dt.UTC), None]  # fmt: skip


def stage_trades(rows):
    def fake(client, ym, ver, counts):
        client.insert("trade_current_staging", rows, column_names=snapshots.COLUMNS)
        counts.update(region_month=0, rollup_month=0, trade_version=0, trade_current=len(rows))

    return fake


def trade_ids(ch, table="trade_current"):
    return [r[0] for r in ch.query(f"SELECT trade_id FROM {table} ORDER BY deal_date, trade_id").result_rows]


def test_publish_writes_month_file_equal_to_what_it_swaps_in(ch, monkeypatch):
    ch.command("TRUNCATE TABLE trade_current")
    rows = [trade("11110-202609-b-0", D(2026, 9, 20), 50000), trade("11110-202609-a-0", D(2026, 9, 3), 40000)]
    store = FakeStore()
    monkeypatch.setattr(publish, "ch", lambda: ch)
    monkeypatch.setattr(publish, "_stage_month", stage_trades(rows))
    counts, f = publish.publish_month("202609", "v9", "00000000-0000-0000-0000-0000000000a1", store)
    assert (
        f.object_key == "trade/deal_ym=202609/00000000-0000-0000-0000-0000000000a1.parquet"
        and f.rows == 2 == counts["trade_current"]
    )
    table = store.files[f.object_key]
    assert table.schema == snapshots.SCHEMA
    assert table.column("trade_id").to_pylist() == trade_ids(ch) == ["11110-202609-a-0", "11110-202609-b-0"]


def test_month_file_failure_leaves_serving_untouched(ch, monkeypatch):
    ch.command("TRUNCATE TABLE trade_current")
    ch.insert("trade_current", [trade("11110-202609-old-0", D(2026, 9, 1), 1)], column_names=snapshots.COLUMNS)
    monkeypatch.setattr(publish, "ch", lambda: ch)
    monkeypatch.setattr(publish, "_stage_month", stage_trades([trade("11110-202609-new-0", D(2026, 9, 2), 2)]))
    with pytest.raises(OSError):
        publish.publish_month("202609", "v9", "00000000-0000-0000-0000-0000000000a2", FakeStore(fail=True))
    assert trade_ids(ch) == ["11110-202609-old-0"]
    assert trade_ids(ch, "trade_current_staging") == []


def test_baseline_writes_every_serving_month_without_changing_serving(ch, monkeypatch):
    ch.command("TRUNCATE TABLE trade_current")
    live = [trade("11110-202608-a-0", D(2026, 8, 1), 1), trade("11110-202609-a-0", D(2026, 9, 1), 2),
            trade("11110-202609-b-0", D(2026, 9, 2), 3)]  # fmt: skip
    ch.insert("trade_current", live, column_names=snapshots.COLUMNS)
    store = FakeStore()
    monkeypatch.setattr(publish, "ch", lambda: ch)
    months, files = publish.snapshot_baseline("base", store)
    assert months == ["202608", "202609"] and [f.rows for f in files] == [1, 2]
    assert trade_ids(ch) == ["11110-202608-a-0", "11110-202609-a-0", "11110-202609-b-0"]


# ───── 변경분 피드 (ADR-045) ─────
def changes(ch, publish_id):
    return [
        (r[0], r[1], tuple(r[2]))
        for r in ch.query(
            "SELECT trade_id, change, changed_fields FROM trade_change WHERE publish_id = {p:UUID} ORDER BY trade_id",
            parameters={"p": publish_id},
        ).result_rows
    ]


def test_publish_records_changes_against_last_recorded_month_file(ch, monkeypatch):
    ch.command("TRUNCATE TABLE trade_current")
    ch.command("TRUNCATE TABLE trade_change")
    store = FakeStore()
    monkeypatch.setattr(publish, "ch", lambda: ch)
    p1, p2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
    first = [trade("11110-202609-a-0", D(2026, 9, 3), 40000), trade("11110-202609-b-0", D(2026, 9, 20), 50000)]
    monkeypatch.setattr(publish, "_stage_month", stage_trades(first))
    counts, f1 = publish.publish_month("202609", "v1", p1, store)  # 기록된 직전 파일 없음 → 모두 추가
    assert counts["trade_change"] == 2 and [c[1] for c in changes(ch, p1)] == ["added", "added"]

    cancelled = trade("11110-202609-b-0", D(2026, 9, 20), 50000)
    cancelled[11], cancelled[12], cancelled[20] = 1, D(2026, 9, 30), 2  # 해제·해제일·버전
    second = [cancelled, trade("11110-202609-c-0", D(2026, 9, 25), 60000)]
    monkeypatch.setattr(publish, "_stage_month", stage_trades(second))
    counts, _ = publish.publish_month("202609", "v2", p2, store, previous_key=f1.object_key)
    assert changes(ch, p2) == [
        ("11110-202609-a-0", "removed", ()),
        ("11110-202609-b-0", "updated", ("cancelled", "cancelDate", "version")),
        ("11110-202609-c-0", "added", ()),
    ]
    assert counts["trade_change"] == 3
    row = ch.query(
        "SELECT is_cancelled, price_manwon FROM trade_change WHERE publish_id = {p:UUID} AND change = 'updated'",
        parameters={"p": p2},
    ).first_row
    assert row == (1, 50000)  # 그 발행 시점 값을 함께 남긴다


def _rent_gold(ym: str, contracts: int) -> dict[str, list[tuple]]:
    """gold 전월세 네 표가 Trino 에서 돌려줄 행 (열 순서는 publish._stage_rent_month 의 SELECT 와 같다)."""
    d = D(int(ym[:4]), int(ym[4:]), 15)
    vf = dt.datetime(2026, 10, 20, 1, 2, 3)
    stats = (contracts, 2, contracts - 2, 1, contracts - 2, 1, 2, 0, 900.0, 1000.0, 1100.0, None, None)
    return {
        "region_rent_month": [("11110", ym, *stats, False)],
        "region_rent_rollup_month": [("11", "sido", ym, *stats, False), ("00", "nation", ym, *stats, False)],
        "rent_serving": [
            (
                f"r11110-{ym}-{i:016x}-0",
                "11110",
                d,
                "c_0123456789abcdef0123",
                "단지",
                "동",
                "1",
                Decimal("84.9"),
                5,
                30000 + i,
                0 if i < 2 else 50,
                i < 2,
                353.4,
                "신규" if i else "",
                "24.08~26.08",
                "",
                -19870 if i == 1 else None,  # 종전 보증금 음수 — 원천 그대로 (부호 있는 열)
                None,
                2004,
                False,
                1,
                vf,
                None,
            )
            for i in range(contracts)
        ],  # fmt: skip
        "rent_version": [
            (f"r11110-{ym}-{i:016x}-0", d, 1, vf, None, True, "신규", "", "", None, None, 2004)
            for i in range(contracts)
        ],  # fmt: skip
    }


def test_rent_month_publish_swaps_four_tables_together(ch, monkeypatch):
    """전월세 달 발행 (ADR-050): 네 표를 함께 스테이징·대조·교체, 시군구×월 표는 연 파티션(같은 해 다른 달 보존)."""
    for t in publish.RENT_TABLES:
        ch.command(f"TRUNCATE TABLE {t}")
    monkeypatch.setattr(publish, "ch", lambda: ch)
    for ym, n in (("202407", 5), ("202408", 6), ("202407", 7)):  # 7월을 다시 발행해도 8월은 그대로
        gold = _rent_gold(ym, n)
        monkeypatch.setattr(publish, "_rows", lambda sql, g=gold: next(v for k, v in g.items() if f"gold.{k} " in sql))
        counts = publish.publish_rent_month(ym, f"gold@t.{ym}")
        assert counts == {"region_rent_month": 1, "rent_rollup_month": 2, "rent_current": n, "rent_version": n}
    months = dict(ch.query("SELECT toYYYYMM(month), contracts FROM region_rent_month ORDER BY 1").result_rows)
    assert months == {202407: 7, 202408: 6}
    cur = ch.query("SELECT toYYYYMM(deal_date), count(), sum(is_jeonse) FROM rent_current GROUP BY 1 ORDER BY 1")
    assert cur.result_rows == [(202407, 7, 2), (202408, 6, 2)]
    assert ch.query("SELECT DISTINCT contract_type FROM rent_current ORDER BY 1").result_rows == [("",), ("신규",)]
    for t in publish.RENT_TABLES:
        assert ch.query(f"SELECT count() FROM {t}_staging").first_row[0] == 0
    assert ch.query("SELECT min(pre_deposit_manwon) FROM rent_current").first_row[0] == -19870


def test_rent_previous_amounts_migration_to_signed(ch):
    """처음 배포한 전월세 표는 종전 금액이 UInt32 였다 — 이관이 값은 그대로 두고 네 표를 함께 Int32 로 바꾼다."""
    for t in ("rent_current", "rent_current_staging", "rent_version", "rent_version_staging"):
        ch.command(f"TRUNCATE TABLE {t}")
        ch.command(f"ALTER TABLE {t} MODIFY COLUMN pre_deposit_manwon Nullable(UInt32), "
                   "MODIFY COLUMN pre_monthly_rent_manwon Nullable(UInt32)")  # fmt: skip
    ch.insert("rent_version", [["r11110-202407-0000000000000000-0", D(2024, 7, 1), 1, dt.datetime(2026, 10, 20), None, 1,
                                "갱신", "", "", 55000, 12, 2004]],
              column_names=["rent_id", "deal_date", "version", "valid_from", "valid_to", "is_current", "contract_type",
                            "contract_term", "use_rr_right", "pre_deposit_manwon", "pre_monthly_rent_manwon",
                            "build_year"])  # fmt: skip
    for stmt in _statements(ROOT / "infra/clickhouse/migrate/rent_signed_pre_amounts.sql"):
        ch.command(stmt)
    types = dict(ch.query(
        "SELECT table || '.' || name, type FROM system.columns WHERE database = 'aptlake' AND name LIKE 'pre_%_manwon'"
    ).result_rows)  # fmt: skip
    assert set(types.values()) == {"Nullable(Int32)"} and len(types) == 8
    assert ch.query("SELECT pre_deposit_manwon, pre_monthly_rent_manwon FROM rent_version").result_rows == [(55000, 12)]
