"""silver 질의에 직접 넣는 값의 형식 검증 — 원천(행안부·국토부 API)에서 온 식별자가 SQL 을 바꾸지 못하게.
단지 차원 문장: 두 원천 모두 그 달 단지를 넣고 처음 관측일을 낮춘다. 빈 값은 매매가 채우고, 전월세는 매매 기록이
없는 단지만 채운다."""

import re
from types import SimpleNamespace

import pytest

from aptlake_pipeline import silver
from aptlake_pipeline.silver import COMPLEX_MERGE, merge_sql, rent_complex_sql, sgg_in_list


def test_sgg_in_list_quotes_valid_codes_and_rejects_anything_else():
    assert sgg_in_list(["11110", "41135"]) == "'11110', '41135'"
    for bad in ["1111", "11110' OR '1'='1", "4113a", ""]:
        with pytest.raises(ValueError):
            sgg_in_list(["11110", bad])


def test_merge_sql_rejects_malformed_month():
    with pytest.raises(ValueError):
        merge_sql("2024-07", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)")


def _insert_pairs(sql: str) -> dict[str, str]:
    cols = re.search(r"INSERT \(([^)]*)\)", sql).group(1).split(", ")
    vals = [v.strip() for v in re.search(r"VALUES \((.*)\)\s*$", sql, re.S).group(1).split(",")]
    assert len(cols) == len(vals)
    return dict(zip(cols, vals, strict=True))


def test_rent_complex_merge_inserts_missing_complexes_and_corrects_rent_only_ones():
    """전월세에만 있는 단지(매매 기록 없음)가 차원에 없어 '이 단지 보기'가 404 였다 — 없는 단지는 넣는다.
    넣을 때만 값을 정하면 처음 반영된 달 기준으로 굳었다(백필은 최신 달부터) → 이미 있는 단지도 처음 관측일을 더 이른
    계약일로 낮춘다. 낮추기를 매매 기록이 없는 단지로만 한정하면 '전월세 먼저 → 매매 → 더 이른 전월세 달' 순서에서 어느
    원천의 최솟값도 아닌 값이 남았다 → 매매 기록과 관계없이 낮춘다(매매 쪽도 낮추기만 — 두 원천 중 가장 이른 날).
    빈 건축년도·일련번호는 매매 기록이 없는 단지만 채운다."""
    sql = rent_complex_sql("202407")
    assert "MERGE INTO lake.silver.apt_complex t" in sql
    assert "FROM lake.stage.apt_rent WHERE deal_ym = '202407'" in sql
    assert sql.count("WHEN NOT MATCHED THEN INSERT") == 1
    assert _insert_pairs(sql) == {
        "complex_key": "s.complex_key",
        "sgg_cd": "s.sgg_cd",
        "umd_nm": "s.umd_nm",
        "jibun": "s.jibun",
        "apt_nm": "s.apt_nm",
        "build_year": "s.build_year",
        "land_leasehold": "CAST(NULL AS boolean)",  # 전월세 원천에 없는 항목 — 미상(NULL)으로 둔다
        "first_seen": "s.first_seen",
        "apt_seq": "s.apt_seq",
    }
    assert "min(deal_date) AS first_seen" in sql
    assert "CASE WHEN count(DISTINCT apt_seq) = 1 THEN min(apt_seq) END AS apt_seq" in sql
    assert "GROUP BY complex_key" in sql  # 단지당 원천 행 하나 → 대상 행 하나에 원천 행 하나 (Trino MERGE 조건)

    # 매매 기록 = silver.apt_trade 의 행 (모든 버전). 토지임대부 NULL 로 가르지 않는다 — 매매도 그 항목이 비면 NULL
    assert (
        "LEFT JOIN (SELECT DISTINCT complex_key FROM lake.silver.apt_trade) x ON x.complex_key = r.complex_key" in sql
    )
    assert "x.complex_key IS NOT NULL AS has_trade" in sql
    matched = re.findall(r"WHEN MATCHED AND (.*?) THEN UPDATE SET\s*(.*?)\s*WHEN NOT MATCHED", sql, re.S)
    assert len(matched) == 1
    cond, sets = (" ".join(x.split()) for x in matched[0])
    assert cond == (
        "(s.first_seen < t.first_seen OR (NOT s.has_trade AND ((t.build_year IS NULL AND s.build_year IS NOT NULL)"
        " OR (t.apt_seq IS NULL AND s.apt_seq IS NOT NULL))))"
    )
    assert sets == (
        "first_seen = least(t.first_seen, s.first_seen),"
        " build_year = CASE WHEN s.has_trade THEN t.build_year ELSE coalesce(t.build_year, s.build_year) END,"
        " apt_seq = CASE WHEN s.has_trade THEN t.apt_seq ELSE coalesce(t.apt_seq, s.apt_seq) END"
    )  # 낮추기·빈 값 채우기만 — 다시 돌려도 같은 값 (멱등), 토지임대부·이름·지번은 건드리지 않는다
    # 매매 쪽도 처음 관측일을 낮추기만 한다 — 두 문장이 같은 규칙이라 반영 순서와 관계없이 두 원천 중 가장 이른 날
    trade = " ".join(COMPLEX_MERGE.split())
    assert "s.first_seen < t.first_seen" in trade and "first_seen = least(t.first_seen, s.first_seen)" in trade
    assert "land_leasehold" not in sets and "land_leasehold" not in cond
    with pytest.raises(ValueError):
        rent_complex_sql("2024-07")


def test_rent_complex_catch_up_uses_the_same_rule_over_all_current_contracts():
    """이미 반영된 달 따라잡기(주간 유지보수): 같은 문장을 silver.apt_rent 현재 행 전체(서빙 범위)에."""
    month, catch_up = rent_complex_sql("202407"), rent_complex_sql()
    assert "FROM lake.silver.apt_rent WHERE is_current" in catch_up and "stage" not in catch_up
    assert catch_up.replace("lake.silver.apt_rent WHERE is_current", "<src>") == month.replace(
        "lake.stage.apt_rent WHERE deal_ym = '202407'", "<src>"
    )


def test_rent_complex_catch_up_reports_inserted_and_fixed(monkeypatch):
    """따라잡기 결과(주간 유지보수 metadata)에 넣은 수와 고친 수가 따로 — 이미 들어간 값을 바로잡은 것도 보인다.
    MERGE 가 돌려주는 행 수는 넣은 행 + 고친 행 (Trino 는 갱신을 한 행으로 센다)."""
    ran: list[str] = []
    results = iter([[(100,)], [(7,)], [(104,)]])  # 차원 수 → MERGE 행 수 → 차원 수
    monkeypatch.setattr(silver, "catalog", lambda: SimpleNamespace(table_exists=lambda t: t == "silver.apt_rent"))
    monkeypatch.setattr(silver, "execute", lambda sql: ran.append(sql) or next(results))
    out = silver.backfill_rent_complexes()
    assert out == {"complexes_before": 100, "complexes_after": 104, "inserted": 4, "fixed": 3}
    assert ran[1] == rent_complex_sql()

    monkeypatch.setattr(silver, "catalog", lambda: SimpleNamespace(table_exists=lambda t: False))
    assert silver.backfill_rent_complexes() == {"skipped": "not created yet"}  # 전월세 첫 반영 전


def test_trade_complex_merge_fills_land_leasehold_only_when_empty():
    """전월세가 먼저 넣은 단지는 토지임대부 여부가 NULL — 나중에 매매가 오면 채우고, 있는 값은 바꾸지 않는다."""
    assert "OR (t.land_leasehold IS NULL AND s.land_leasehold IS NOT NULL)) THEN UPDATE SET" in COMPLEX_MERGE
    assert "land_leasehold = coalesce(t.land_leasehold, s.land_leasehold)" in COMPLEX_MERGE
    assert _insert_pairs(COMPLEX_MERGE)["land_leasehold"] == "s.land_leasehold"


@pytest.mark.parametrize(
    ("spec", "silver_table", "complex_sql"),
    [
        (silver.TRADE_MERGE, "silver.apt_trade", COMPLEX_MERGE.format(deal_ym="202407")),
        (silver.RENT_MERGE, "silver.apt_rent", rent_complex_sql("202407")),
    ],
)
def test_month_merge_updates_complex_dimension_right_after_scd2_merge(monkeypatch, spec, silver_table, complex_sql):
    ran: list[str] = []
    part = {"sgg_cd": "11110", "last_ingest_id": "202407-11110-20261020T000000-x", "fetch_count": 1}
    table = SimpleNamespace(overwrite=lambda *a, **k: None, current_snapshot=lambda: None)
    monkeypatch.setattr(silver.ops_db, "loaded_partitions", lambda ym, source: [part])
    monkeypatch.setattr(silver, "_stage_rows", lambda ym, parts, spec: [])
    monkeypatch.setattr(silver, "ensure_schema", lambda: None)
    monkeypatch.setattr(silver, "catalog", lambda: SimpleNamespace(load_table=lambda name: table))
    monkeypatch.setattr(silver, "execute", lambda sql: ran.append(sql) or [(0, 0)])
    monkeypatch.setattr(silver, "post_merge_checks", lambda *a, **k: [])
    for name in ("record_dq", "mark_merged", "mark_month_merged"):
        monkeypatch.setattr(silver.ops_db, name, lambda *a, **k: None)

    silver.merge_month("202407", spec=spec)
    merges = [s for s in ran if "MERGE INTO" in s]
    assert len(merges) == 2 and f"MERGE INTO lake.{silver_table} t" in merges[0]
    assert merges[1] == complex_sql
