"""Iceberg 압축 분할: 한 문장이 여는 파티션 수가 Trino 한도(100)를 넘지 않게 (ICEBERG_TOO_MANY_OPEN_PARTITIONS 회귀).
주간 정리 앞의 멱등 보정(전월세에만 있는 단지 → 단지 차원)의 순서·실패 격리."""

from types import SimpleNamespace

import pytest

from aptlake_pipeline import maintenance, silver, snapshots
from aptlake_pipeline.maintenance import MaintenanceError, partition_filters, year_filters


def test_filters_group_by_month_and_cap_writers_per_statement():
    parts = [["202101", f"{11000 + i}"] for i in range(200)] + [["202102", "41135"]]
    filters = partition_filters(["deal_ym", "sgg_cd"], parts, max_writers=90)
    assert len(filters) == 4  # 202101: 90 + 90 + 20, 202102: 1
    assert all(f.count("'") // 2 - 1 <= 90 for f in filters)
    assert filters[0].startswith("deal_ym = '202101' AND sgg_cd IN ('11000', ")
    assert filters[-1] == "deal_ym = '202102' AND sgg_cd IN ('41135')"
    # 모든 파티션이 정확히 한 번씩 포함
    covered = sum(f.count("', '") + 1 for f in filters)
    assert covered == 201


def test_single_column_partitions_and_duplicates():
    assert partition_filters(["deal_ym"], [["202101"], ["202102"], ["202101"]]) == ["deal_ym IN ('202101', '202102')"]
    assert partition_filters(["deal_ym"], []) == []


def test_year_partitions_are_compacted_one_year_per_statement():
    """연 변환 파티션 값은 1970 년부터 센 연 수 — 경계는 변환과 맞는 [1월 1일, 이듬해 1월 1일)."""
    assert year_filters("deal_date", [53, 52, 53]) == [
        "deal_date >= DATE '2022-01-01' AND deal_date < DATE '2023-01-01'",
        "deal_date >= DATE '2023-01-01' AND deal_date < DATE '2024-01-01'",
    ]
    assert year_filters("deal_date", []) == []
    with pytest.raises(ValueError):
        year_filters("deal_date) OR (1=1", [52])
    with pytest.raises(ValueError):
        year_filters("deal_date", [-1])


def _fake_lake(monkeypatch, ddl: str, parts: list[tuple], n_parts: int = 5) -> list[str]:
    from aptlake_pipeline import maintenance

    sql: list[str] = []

    def execute(q: str):
        sql.append(q)
        if q.startswith("SHOW CREATE TABLE"):
            return [(ddl,)]
        if "count(*)" in q and "$partitions" in q:
            return [(n_parts,)]
        if "$partitions" in q:
            return parts
        return []

    monkeypatch.setattr(maintenance, "execute", execute)
    return sql


def test_optimize_year_partitioned_table_per_year(monkeypatch):
    """전월세 silver(연 변환 + 정렬 쓰기) 한 문장 압축이 Trino 힙을 넘겼다 — 파일이 2개 이상인 연도마다 한 문장."""
    from aptlake_pipeline import maintenance

    ddl = "CREATE TABLE lake.silver.apt_rent (...) WITH (partitioning = ARRAY['year(deal_date)'], sorted_by = ARRAY['sgg_cd'])"
    sql = _fake_lake(monkeypatch, ddl, [(56,), (52,)])
    assert maintenance._optimize("silver.apt_rent") == 2
    optimize = [q for q in sql if q.startswith("ALTER TABLE")]
    assert optimize == [
        f"ALTER TABLE lake.silver.apt_rent {maintenance.OPTIMIZE} WHERE deal_date >= DATE '2022-01-01' AND deal_date < DATE '2023-01-01'",
        f"ALTER TABLE lake.silver.apt_rent {maintenance.OPTIMIZE} WHERE deal_date >= DATE '2026-01-01' AND deal_date < DATE '2027-01-01'",
    ]
    assert any('partition.deal_date_year FROM lake.silver."apt_rent$partitions" WHERE file_count > 1' in q for q in sql)


def test_optimize_small_identity_table_in_one_statement(monkeypatch):
    from aptlake_pipeline import maintenance

    sql = _fake_lake(monkeypatch, "CREATE TABLE x WITH (partitioning = ARRAY['deal_ym'])", [], n_parts=70)
    assert maintenance._optimize("gold.rent_serving") == 1
    assert [q for q in sql if q.startswith("ALTER TABLE")] == [
        f"ALTER TABLE lake.gold.rent_serving {maintenance.OPTIMIZE}"
    ]


def test_rejects_values_that_could_break_the_statement():
    with pytest.raises(ValueError):
        partition_filters(["sgg_cd"], [["11110' OR 1=1 --"]])


@pytest.fixture
def lake(monkeypatch):
    """유지보수가 부르는 레이크 쪽을 가짜로 — 실행한 문장과 순서만 본다."""
    calls: list[str] = []
    monkeypatch.setattr(maintenance, "TABLES", ["silver.apt_complex"])
    monkeypatch.setattr(maintenance, "catalog", lambda: SimpleNamespace(table_exists=lambda t: True))
    monkeypatch.setattr(maintenance, "_stats", lambda t: {"files": 2, "snapshots": 3})
    monkeypatch.setattr(maintenance, "_optimize", lambda t: calls.append(f"optimize {t}") or 1)
    monkeypatch.setattr(maintenance, "execute", lambda sql: calls.append(sql.split(" EXECUTE ")[1].split("(")[0]))
    monkeypatch.setattr(snapshots, "cleanup", lambda: {"deleted": 0})
    monkeypatch.setattr(silver, "backfill_rent_complexes", lambda: calls.append("rent_complexes") or {"inserted": 4553})
    return SimpleNamespace(run=maintenance.run, calls=calls, silver=silver, monkeypatch=monkeypatch)


def test_rent_complex_catch_up_runs_before_table_cleanup(lake):
    """전월세에만 있는 단지 보정(멱등)을 먼저 — 새로 쓴 단지 차원 파일이 같은 실행의 압축 대상이 된다."""
    out = lake.run()
    assert lake.calls == ["rent_complexes", "optimize silver.apt_complex", "expire_snapshots", "remove_orphan_files"]
    assert out["rent_complexes"] == {"inserted": 4553}


def test_rent_complex_catch_up_failure_does_not_stop_cleanup(lake):
    def boom():
        raise RuntimeError("trino down")

    lake.monkeypatch.setattr(lake.silver, "backfill_rent_complexes", boom)
    with pytest.raises(MaintenanceError, match="rent_complexes"):
        lake.run()
    assert lake.calls == ["optimize silver.apt_complex", "expire_snapshots", "remove_orphan_files"]


def test_optimize_bronze_identity_partitions_in_writer_sized_chunks(monkeypatch):
    """파티션이 많은 항등 파티션 표(bronze 1.7만 개)는 앞 열 값 + 마지막 열 IN 으로 나눈다 (한 문장이면 파티션 상한 초과)."""
    from aptlake_pipeline import maintenance

    ddl = "CREATE TABLE lake.bronze.rtms_raw (...) WITH (partitioning = ARRAY['deal_ym','sgg_cd'])"
    sql = _fake_lake(monkeypatch, ddl, [("202101", "11110"), ("202101", "11140")], n_parts=17609)
    assert maintenance._optimize("bronze.rtms_raw") == 1
    assert [q for q in sql if q.startswith("ALTER TABLE")] == [
        f"ALTER TABLE lake.bronze.rtms_raw {maintenance.OPTIMIZE} WHERE deal_ym = '202101' AND sgg_cd IN ('11110', '11140')"
    ]
    assert any('partition.deal_ym, partition.sgg_cd FROM lake.bronze."rtms_raw$partitions"' in q for q in sql)


def _table_loop(monkeypatch, tables: list[str], optimize, select_one):
    from aptlake_pipeline import maintenance, silver, snapshots

    done: list[str] = []
    monkeypatch.setattr(maintenance, "TABLES", tables)
    monkeypatch.setattr(maintenance, "catalog", lambda: SimpleNamespace(table_exists=lambda t: True))
    monkeypatch.setattr(maintenance, "_stats", lambda t: {"files": 2, "snapshots": 3})
    monkeypatch.setattr(maintenance, "_optimize", optimize)
    monkeypatch.setattr(silver, "backfill_rent_complexes", lambda: {"inserted": 0, "fixed": 0})
    monkeypatch.setattr(snapshots, "cleanup", lambda: {"deleted": 0})
    monkeypatch.setattr(maintenance.time, "sleep", lambda s: None)

    def execute(q: str):
        if q == "SELECT 1":
            return select_one()
        done.append(q.split(" EXECUTE ")[0].removeprefix("ALTER TABLE lake."))
        return []

    monkeypatch.setattr(maintenance, "execute", execute)
    return maintenance, done


def test_waits_for_trino_after_a_table_brings_it_down(monkeypatch):
    """한 표의 압축이 Trino 를 다시 띄우면 다시 받을 때까지 기다린 뒤 남은 표를 정리한다 (2026-10-21: 기다리지 않아 4개 표가 연결 오류)."""

    def optimize(t: str) -> int:
        if t == "silver.apt_rent":
            raise ConnectionError("failed to fetch")
        return 1

    probes = iter([ConnectionError("refused"), ConnectionError("refused"), [(1,)]])

    def select_one():
        r = next(probes)
        if isinstance(r, Exception):
            raise r
        return r

    maintenance, done = _table_loop(monkeypatch, ["silver.apt_rent", "gold.rent_serving"], optimize, select_one)
    with pytest.raises(MaintenanceError) as e:
        maintenance.run()
    assert "silver.apt_rent" in str(e.value) and "'trino'" not in str(e.value)  # 다시 떴으니 건너뛴 표 없음
    assert done == ["gold.rent_serving", "gold.rent_serving"]  # 만료·고아 정리까지 했다


def test_skips_remaining_tables_when_trino_does_not_come_back(monkeypatch):
    def optimize(t: str) -> int:
        raise ConnectionError("failed to fetch")

    def select_one():
        raise ConnectionError("refused")

    maintenance, done = _table_loop(monkeypatch, ["silver.apt_rent", "gold.rent_serving"], optimize, select_one)
    with pytest.raises(MaintenanceError, match="remaining tables skipped"):
        maintenance.run()
    assert done == []
