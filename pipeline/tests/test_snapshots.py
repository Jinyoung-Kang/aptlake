"""달별 스냅숏 파일 (ADR-044): 버전 → 파일 규칙, 보관 정리 대상, 파일 형식."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from aptlake_pipeline import snapshots

CASES = json.loads((Path(__file__).parent / "fixtures/snapshot_versions.json").read_text())


def _versions():
    return [{**v, "published_at": dt.datetime.fromisoformat(v["published_at"])} for v in CASES["versions"]]


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["why"])
def test_files_for_version_follows_shared_case_table(case):
    got = snapshots.files_for_version(_versions(), CASES["files"], case["target"])
    if case["expect"] is None:
        assert got is None
    else:
        assert [f"{f['publish_id'][:8]}:{f['deal_ym']}" for f in got] == case["expect"]


def _v(name: str, at: dt.datetime, pid: str | None, baseline: bool = False) -> dict:
    return {"version": name, "published_at": at, "publish_id": pid, "snapshot_baseline": baseline}


def test_kept_versions_recent_days_plus_last_of_each_month_plus_latest():
    now = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
    vs = [
        _v("old-jun-a", dt.datetime(2026, 6, 3, tzinfo=dt.UTC), "a"),
        _v("old-jun-b", dt.datetime(2026, 6, 20, tzinfo=dt.UTC), "b"),  # 6월(KST) 마지막
        _v("aug", dt.datetime(2026, 8, 31, 16, tzinfo=dt.UTC), "c"),  # KST 9월 1일 01시 → 9월로 센다
        _v("sep-mid", dt.datetime(2026, 9, 5, tzinfo=dt.UTC), "d"),
        _v("recent", dt.datetime(2026, 9, 20, tzinfo=dt.UTC), "e"),  # 30일 안
        _v("ancient", dt.datetime(2025, 9, 1, tzinfo=dt.UTC), "f", baseline=True),  # 기준선, 1년 넘음
        _v("pre-feature", dt.datetime(2026, 10, 1, tzinfo=dt.UTC), None),
    ]
    assert snapshots.kept_versions(vs, now) == {"old-jun-b", "recent"}


def test_files_to_delete_keeps_files_still_used_by_kept_versions():
    now = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
    vs = [
        _v("base", dt.datetime(2026, 7, 1, tzinfo=dt.UTC), "base", baseline=True),
        _v("jul-late", dt.datetime(2026, 7, 2, tzinfo=dt.UTC), "j2"),  # 7월 마지막 → 남음
        _v("new", dt.datetime(2026, 10, 9, tzinfo=dt.UTC), "new"),
    ]
    vs.insert(1, _v("jul-mid", dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC), "j1"))
    vs.insert(0, _v("pre-base", dt.datetime(2026, 6, 30, tzinfo=dt.UTC), "pre"))  # 기준선 전 발행
    files = [
        {"publish_id": "base", "deal_ym": "202606", "object_key": "k0"},  # 모든 버전이 씀
        {"publish_id": "base", "deal_ym": "202607", "object_key": "k1"},  # j1 이 덮음 → 아무도 안 씀
        {"publish_id": "j1", "deal_ym": "202607", "object_key": "k2"},  # j2 가 덮음 → 아무도 안 씀
        {"publish_id": "j2", "deal_ym": "202607", "object_key": "k3"},  # 7월 마지막·최신이 씀
        {"publish_id": "j1", "deal_ym": "202605", "object_key": "k4", "deleted_at": now},  # 이미 지움
        {"publish_id": "pre", "deal_ym": "202606", "object_key": "k5"},  # 기준선 전 파일 — 쓰이지 않는다
    ]
    assert [f["object_key"] for f in snapshots.files_to_delete(vs, files, now)] == ["k1", "k2", "k5"]


def test_orphan_keys_only_unrecorded_and_older_than_grace():
    now = dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
    files = [{"object_key": "trade/deal_ym=202609/a.parquet"}]
    keys = [
        ("trade/deal_ym=202609/a.parquet", now - dt.timedelta(days=3)),  # 기록 있음
        ("trade/deal_ym=202609/x.parquet", now - dt.timedelta(days=3)),  # 실패한 발행
        ("trade/deal_ym=202609/y.parquet", now - dt.timedelta(hours=1)),  # 진행 중일 수 있음
    ]
    assert snapshots.orphan_keys(keys, files, now) == ["trade/deal_ym=202609/x.parquet"]


def test_write_parquet_fixes_schema_and_keeps_empty_months(tmp_path):
    """ClickHouse 는 LowCardinality 를 사전 인코딩으로 줄 수 있다 — 파일마다 같은 형식이어야 이어 붙일 수 있다."""
    row = {
        "trade_id": ["11110-202609-0000000000000000-0"], "sgg_cd": pa.array(["11110"]).dictionary_encode(),
        "deal_date": [dt.date(2026, 9, 1)], "complex_key": ["c"], "apt_nm": ["a"],
        "umd_nm": pa.array(["숭인동"]).dictionary_encode(), "jibun": ["1"],
        "area_m2": pa.array([84.99], pa.float64()).cast(pa.decimal128(9, 4)), "floor": pa.array([5], pa.int16()),
        "price_manwon": pa.array([50000], pa.uint32()), "ppm2": [588.3], "is_cancelled": pa.array([0], pa.uint8()),
        "cancel_date": pa.array([None], pa.date32()), "registered_date": pa.array([None], pa.date32()),
        "apt_dong": [""], "deal_kind": ["중개거래"], "seller_type": ["개인"], "buyer_type": ["개인"],
        "build_year": pa.array([2000], pa.uint16()), "is_outlier": pa.array([0], pa.uint8()),
        "version": pa.array([1], pa.uint16()),
        "valid_from": pa.array([dt.datetime(2026, 9, 2, tzinfo=dt.UTC)], pa.timestamp("ms", tz="UTC")),
        "missing_since": pa.array([None], pa.timestamp("ms", tz="UTC")),
    }  # fmt: skip
    one = tmp_path / "one.parquet"
    assert snapshots.write_parquet([pa.table(row)], str(one)) == 1
    assert pq.read_schema(one) == snapshots.SCHEMA
    empty = tmp_path / "empty.parquet"
    assert snapshots.write_parquet([], str(empty)) == 0
    assert pq.read_table(empty).schema == snapshots.SCHEMA


def test_object_key_and_query_reject_malformed_input():
    assert snapshots.object_key("202609", "p") == "trade/deal_ym=202609/p.parquet"
    for bad in ("2026-09", "202609; DROP"):
        with pytest.raises(ValueError):
            snapshots.object_key(bad, "p")
    with pytest.raises(ValueError):
        snapshots.month_query("region_month", "202609")


SAMPLE = Path(__file__).parent / "fixtures/snapshot_sample.parquet"


def sample_rows() -> pa.Table:
    """API 내보내기 시험이 읽는 달 스냅숏 견본 — 파이프라인이 쓰는 형식 그대로 (두 패키지 사이의 계약)."""
    n = ["11110-202609-0000000000000001-0", "11110-202609-0000000000000002-0", "41135-202609-0000000000000003-0"]
    return pa.table(
        {
            "trade_id": n, "sgg_cd": ["11110", "11110", "41135"],
            "deal_date": [dt.date(2026, 9, 1), dt.date(2026, 9, 15), dt.date(2026, 9, 20)],
            "complex_key": ["c1", "c1", "c2"], "apt_nm": ["가", "가", "나"], "umd_nm": ["숭인동", "숭인동", "정자동"],
            "jibun": ["1", "1", "2"], "area_m2": pa.array([84.99, 59.9, 101.5]).cast(pa.decimal128(9, 4)),
            "floor": pa.array([5, None, 12], pa.int16()), "price_manwon": pa.array([50000, 40000, 150000], pa.uint32()),
            "ppm2": [588.3, 667.8, 1477.8], "is_cancelled": pa.array([0, 1, 0], pa.uint8()),
            "cancel_date": pa.array([None, dt.date(2026, 9, 30), None], pa.date32()),
            "registered_date": pa.array([None, None, dt.date(2026, 10, 2)], pa.date32()),
            "apt_dong": ["", "", "101"], "deal_kind": ["중개거래"] * 3, "seller_type": ["개인"] * 3,
            "buyer_type": ["개인"] * 3, "build_year": pa.array([2000, 2000, 2015], pa.uint16()),
            "is_outlier": pa.array([0, 0, 0], pa.uint8()), "version": pa.array([1, 2, 1], pa.uint16()),
            "valid_from": pa.array([dt.datetime(2026, 9, 2, tzinfo=dt.UTC)] * 3, pa.timestamp("ms", tz="UTC")),
            "missing_since": pa.array([None] * 3, pa.timestamp("ms", tz="UTC")),
        }
    )  # fmt: skip


def test_sample_fixture_matches_writer_schema(tmp_path):
    """견본 파일이 지금 쓰는 형식과 같은가 — 형식을 바꾸면 견본을 다시 만들어 API 시험도 함께 확인하게 한다.
    다시 만들기: SNAPSHOT_SAMPLE_WRITE=1 uv run pytest tests/test_snapshots.py -k sample"""
    import os

    if os.environ.get("SNAPSHOT_SAMPLE_WRITE"):
        snapshots.write_parquet([sample_rows()], str(SAMPLE))
    assert pq.read_schema(SAMPLE) == snapshots.SCHEMA
    assert pq.read_table(SAMPLE).equals(sample_rows().cast(snapshots.SCHEMA))
