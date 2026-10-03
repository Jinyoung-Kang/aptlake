"""silver 질의에 직접 넣는 값의 형식 검증 — 원천(행안부·국토부 API)에서 온 식별자가 SQL 을 바꾸지 못하게."""

import pytest

from aptlake_pipeline.silver import merge_sql, sgg_in_list


def test_sgg_in_list_quotes_valid_codes_and_rejects_anything_else():
    assert sgg_in_list(["11110", "41135"]) == "'11110', '41135'"
    for bad in ["1111", "11110' OR '1'='1", "4113a", ""]:
        with pytest.raises(ValueError):
            sgg_in_list(["11110", bad])


def test_merge_sql_rejects_malformed_month():
    with pytest.raises(ValueError):
        merge_sql("2024-07", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)")
