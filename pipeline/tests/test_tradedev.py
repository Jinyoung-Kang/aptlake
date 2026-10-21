"""매매 상세판(TradeDev) 전환 (ADR-049): 공개 거래 ID 는 그대로, 새 항목은 보강 전용 열.

fixture 둘은 2026-10-20 같은 날 같은 파티션(11110/202407)을 기본판·상세판으로 받은 실제 응답이다.
"""

from __future__ import annotations

import re
from pathlib import Path

from aptlake_pipeline.ingest import row_matches_schema, validate
from aptlake_pipeline.lake import ENRICH_COLUMNS
from aptlake_pipeline.rtms.client import FetchResult
from aptlake_pipeline.rtms.fingerprint import MUTABLE_FIELDS, assign_keys
from aptlake_pipeline.rtms.parse import (
    SOURCE_FIELDS_V1,
    SOURCE_FIELDS_V2,
    fields_for,
    parse_page,
    schema_version_of,
    to_typed,
)
from aptlake_pipeline.silver import DATA_COLS, merge_sql

FIX = Path(__file__).parent / "fixtures"
BASIC = parse_page((FIX / "rtms_11110_202407.xml").read_text()).items
DEV = parse_page((FIX / "rtms_dev_11110_202407.xml").read_text()).items


def _result(items_xml: str) -> FetchResult:
    return FetchResult("11110", "202407", [items_xml.encode()], [parse_page(items_xml)], 0.0, 1)


def test_dev_page_parses_with_v2_fields():
    assert len(DEV) == 60
    assert all(set(i) == set(SOURCE_FIELDS_V2) for i in DEV)  # 계약: 실제 응답의 항목 집합 = 명세 v2
    typed = [to_typed(i) for i in DEV]
    assert all(t.apt_seq and t.apt_seq.startswith("11110-") for t in typed)
    assert all(t.road_nm and t.umd_cd for t in typed)
    # 기본판 행은 보강 항목이 모두 None (같은 TypedTrade 로 읽힌다)
    assert all(getattr(to_typed(i), c) is None for i in BASIC for c in ENRICH_COLUMNS)


def test_dev_keeps_public_ids_unchanged():
    """공통 20개 항목이 바이트 단위로 같으므로 지문·순번·속성 해시 집합이 같다 → 거래 ID·변경 피드 그대로."""
    common = sorted(tuple(r[f] for f in SOURCE_FIELDS_V1) for r in DEV)
    assert common == sorted(tuple(r[f] for f in SOURCE_FIELDS_V1) for r in BASIC)
    keys = lambda items: {(k.trade_id, k.attr_hash, k.complex_key) for k in assign_keys(to_typed(r) for r in items)}  # noqa: E731
    assert keys(DEV) == keys(BASIC)
    # 새 항목은 속성 해시에 들어가지 않는다 (넣으면 270만 건이 모두 새 버전)
    assert not set(ENRICH_COLUMNS) & set(MUTABLE_FIELDS)


def test_schema_version_is_decided_by_the_payload():
    assert schema_version_of(BASIC) == 1
    assert schema_version_of(DEV) == 2
    assert schema_version_of([]) == 1
    assert schema_version_of([{**DEV[0], "newField": "x"}]) is None
    assert set(fields_for(1)) < set(fields_for(2))


def test_validate_accepts_both_versions_and_quarantines_unknown_fields():
    for name in ("rtms_11110_202407.xml", "rtms_dev_11110_202407.xml"):
        checks = {c.name: c for c in validate(_result((FIX / name).read_text()), 60)}
        assert checks["bronze_schema_matches_v1"].passed, name
    xml = (FIX / "rtms_dev_11110_202407.xml").read_text().replace("<aptSeq>", "<newField>x</newField><aptSeq>", 1)
    check = {c.name: c for c in validate(_result(xml), 60)}["bronze_schema_matches_v1"]
    assert not check.passed and check.blocking and check.metric["unknown_fields"] == ["newField"]


def test_loaded_row_check_uses_the_rows_own_version():
    assert row_matches_schema(BASIC[0], 1) and row_matches_schema(DEV[0], 2) and row_matches_schema(BASIC[0], 2)
    assert not row_matches_schema(DEV[0], 1)  # v1 로 적힌 행에 v2 항목이 있으면 불일치
    assert not row_matches_schema(DEV[0], 3)  # 모르는 판
    assert not row_matches_schema({**DEV[0], "dealAmount": "x"}, 2)  # 파싱 실패


def test_merge_fills_enrichment_on_unchanged_rows_without_new_version():
    sql = merge_sql("202407", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)")
    same_branch = sql.split("WHEN MATCHED THEN UPDATE SET", 1)[1].split("WHEN NOT MATCHED", 1)[0]
    for c in ENRICH_COLUMNS:
        assert f"{c} = coalesce(src.{c}, t.{c})" in same_branch
        assert c in DATA_COLS  # 새 거래·새 버전에는 INSERT 로 들어간다
    # '속성 다름' 분기(현재행 닫기)는 보강 열을 건드리지 않는다
    close_branch = sql.split("WHEN MATCHED AND t.attr_hash <> src.attr_hash THEN UPDATE SET", 1)[1].split("WHEN", 1)[0]
    assert "coalesce" not in close_branch


def test_merge_branches_line_up_with_stage_column_order():
    """UNION ALL 세 갈래는 위치로 맞춰진다 — 보강 열이 수집 열(ingest_id…) 뒤에 붙어도 MISSING 갈래가 같은 순서여야 한다."""
    from aptlake_pipeline.silver import STAGE_COLS

    sql = merge_sql("202407", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)")
    stage_select = sql.split("SELECT ", 2)[1].split(" FROM lake.stage.apt_trade", 1)[0]
    assert [c.strip().removeprefix("st.") for c in stage_select.split(",")] == STAGE_COLS
    missing = sql.split("'MISSING', NULL, ", 1)[1].split("\n    FROM cur c", 1)[0]
    parts = re.findall(r"[cp]\.\w+|CAST\(NULL AS [^)]*\)\)?", missing)  # decimal(9,4) 의 쉼표는 나누지 않는다
    assert len(parts) == len(STAGE_COLS)
    assert parts[STAGE_COLS.index("ingest_id")] == "p.ingest_id"
    assert parts[STAGE_COLS.index("apt_seq")] == "CAST(NULL AS varchar)"
    assert STAGE_COLS.index("apt_seq") > STAGE_COLS.index("fetch_seq")  # 표에 뒤로 붙은 순서 그대로


def _newver(sql: str, key: str) -> tuple[list[str], str]:
    """NEWVER 갈래의 stage 열 목록과 그 갈래가 조인하는 이전 현재행(cur)의 열 목록."""
    branch = sql.split("'NEWVER', c.version + 1, ", 1)[1].split("\n    FROM s JOIN cur c", 1)[0]
    cur = sql.split("cur AS (\n        SELECT ", 1)[1].split(" FROM ", 1)[0]
    assert cur.startswith(f"{key}, dup_seq, sgg_cd, attr_hash, version")
    return re.findall(r"coalesce\(s\.\w+, c\.\w+\)|s\.\w+", branch), cur  # coalesce 안의 쉼표는 나누지 않는다


def test_new_version_keeps_enrichment_when_stage_has_none():
    """상세판 롤백(기본판 v1) 동안 속성이 바뀐 거래: 새 버전은 stage 의 보강 열이 비면 이전 현재행 값을 잇는다.
    stage 값을 그대로 넣으면 이미 채운 일련번호·도로명이 새 현재행에서 NULL 이 됐다 ('속성 같음' 분기와 같은 규칙)."""
    from aptlake_pipeline.silver import STAGE_COLS

    sql = merge_sql("202407", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)")
    cols, cur = _newver(sql, "trade_key")
    # 위치로 맞춰지는 갈래 — stage 열 순서 그대로, 보강 열만 coalesce(stage, 이전 현재행)
    assert cols == [f"coalesce(s.{c}, c.{c})" if c in ENRICH_COLUMNS else f"s.{c}" for c in STAGE_COLS]
    assert all(f", {c}" in cur for c in ENRICH_COLUMNS)
