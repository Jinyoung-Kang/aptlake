"""Iceberg 압축 분할: 한 문장이 여는 파티션 수가 Trino 한도(100)를 넘지 않게 (ICEBERG_TOO_MANY_OPEN_PARTITIONS 회귀).
주간 정리 앞의 멱등 보정(전월세에만 있는 단지 → 단지 차원)의 순서·실패 격리."""

from types import SimpleNamespace

import pytest

from aptlake_pipeline import maintenance, silver, snapshots
from aptlake_pipeline.maintenance import MaintenanceError, partition_filters


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
