"""Iceberg 압축 분할: 한 문장이 여는 파티션 수가 Trino 한도(100)를 넘지 않게 (ICEBERG_TOO_MANY_OPEN_PARTITIONS 회귀)."""

import pytest

from aptlake_pipeline.maintenance import partition_filters


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
