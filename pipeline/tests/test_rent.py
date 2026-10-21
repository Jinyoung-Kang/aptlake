"""전월세 (ADR-050): 파서·지문·수집 검사·MERGE 문장 — 매매와 같은 규칙인지.

fixture 는 2026-10-20 실제 응답(11110 종로구 2024-07 전월세 161건)이고, 매매 상세판 fixture 와 같은 날 같은 파티션이다.
"""

from __future__ import annotations

import dataclasses
import random
import re
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aptlake_pipeline.ingest import row_matches_schema, validate
from aptlake_pipeline.rtms.client import FetchResult
from aptlake_pipeline.rtms.fingerprint import complex_key
from aptlake_pipeline.rtms.parse import parse_page, to_typed
from aptlake_pipeline.rtms.rent_fingerprint import RENT_MUTABLE_FIELDS, assign_rent_keys, rent_attr_hash, rent_key
from aptlake_pipeline.rtms.rent_parse import RENT_FIELDS_V1, rent_schema_version_of, to_typed_rent
from aptlake_pipeline.silver import RENT_MERGE, merge_sql
from aptlake_pipeline.sources import RENT

FIX = Path(__file__).parent / "fixtures"
XML = (FIX / "rent_11110_202407.xml").read_text()
ITEMS = parse_page(XML).items


def _result(xml: str) -> FetchResult:
    return FetchResult("11110", "202407", [xml.encode()], [parse_page(xml)], 0.0, 1)


def test_real_rent_page_parses_all_items():
    page = parse_page(XML)
    assert page.total_count == len(ITEMS) == 161
    assert all(set(i) == set(RENT_FIELDS_V1) for i in ITEMS)  # 계약: 실제 응답의 항목 집합 = 탐침으로 확정한 v1
    assert rent_schema_version_of(ITEMS) == 1
    typed = [to_typed_rent(i) for i in ITEMS]
    assert all(t.sgg_cd == "11110" and t.deal_date.strftime("%Y%m") == "202407" for t in typed)
    # 월세 0 = 전세 (원천 monthlyRent 는 늘 숫자): 원문 '0' 의 수와 같다
    assert sum(t.monthly_rent_manwon == 0 for t in typed) == sum(i["monthlyRent"].strip() == "0" for i in ITEMS) == 89
    assert {t.contract_type for t in typed} == {"신규", "갱신", None}  # 공백은 미상(None), 원문을 바꾸지 않는다
    assert {t.use_rr_right for t in typed} == {"사용", None}
    assert all(t.apt_seq and t.apt_seq.startswith("11110-") for t in typed)
    # 보증금 0 인 월세 계약도 있다 (2건) — 보증금·월세가 둘 다 0 인 계약은 없다
    assert sum(t.deposit_manwon == 0 for t in typed) == 2
    assert not any(t.deposit_manwon == 0 and t.monthly_rent_manwon == 0 for t in typed)
    assert any(t.pre_deposit_manwon for t in typed) and any(t.road_nm for t in typed)


def test_negative_previous_deposit_is_kept_as_sent():
    """원천이 종전 보증금에 음수를 보낸다(2022년 일부 갱신 계약, 41360/202206 에 14건) — 뜻을 해석하지 않고 부호까지 보관한다.
    예전에는 파싱 실패로 파티션 전체가 격리됐다. 보증금·월세(지문·필수)는 여전히 음수를 받지 않는다."""
    t = to_typed_rent({**ITEMS[0], "contractType": "갱신", "preDeposit": "-19,870", "preMonthlyRent": "5"})
    assert (t.pre_deposit_manwon, t.pre_monthly_rent_manwon) == (-19870, 5)
    with pytest.raises(
        ValueError, match="invalid deposit"
    ):  # 어느 항목인지 오류에 이름이 나온다 (예전엔 늘 dealAmount)
        to_typed_rent({**ITEMS[0], "deposit": "-3,500"})
    with pytest.raises(ValueError, match="invalid monthlyRent"):
        to_typed_rent({**ITEMS[0], "monthlyRent": "-1"})
    neg = XML.replace("<preDeposit> </preDeposit>", "<preDeposit>-19,870</preDeposit>", 1)
    assert neg != XML
    checks = {c.name: c for c in validate(_result(neg), 161, spec=RENT)}
    assert checks["rows_parse_and_belong_to_partition"].passed


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"floor": "3층"}, "invalid floor: '3층'"),
        ({"buildYear": "x"}, "invalid buildYear: 'x'"),
        ({"dealDay": "a"}, "invalid dealDay: 'a'"),
        ({"dealYear": "2024", "dealMonth": "13", "dealDay": "1"}, "invalid deal date: '2024'-'13'-'1'"),
        ({"dealYear": "2024", "dealMonth": "2", "dealDay": "30"}, "invalid deal date: '2024'-'2'-'30'"),
    ],
)
def test_parse_errors_name_the_source_field_and_raw_value(bad, message):
    """파싱 격리의 원인은 ops.dq_result 의 metric.sample(예외 문구)로 찾는다 — 금액뿐 아니라 정수·날짜 항목도 어느 항목의
    어떤 원문인지 나와야 한다 (예전엔 'invalid integer: …', 없는 날짜는 'month must be in 1..12' 로 원문도 없었다)."""
    with pytest.raises(ValueError) as e:
        to_typed_rent({**ITEMS[0], **bad})
    assert str(e.value) == message
    broken = XML
    for tag, value in bad.items():  # 같은 문구가 수집 검사 기록에 그대로 남는다 (원본의 첫 행만 고친다)
        broken = re.sub(rf"<{tag}>[^<]*</{tag}>", f"<{tag}>{value}</{tag}>", broken, count=1)
    check = {c.name: c for c in validate(_result(broken), 161, spec=RENT)}["rows_parse_and_belong_to_partition"]
    assert not check.passed and check.metric["sample"] == [message]


def test_required_fields_and_amounts():
    with pytest.raises(ValueError, match="monthlyRent"):
        to_typed_rent({**ITEMS[0], "monthlyRent": " "})
    t = to_typed_rent({**ITEMS[0], "deposit": "143,500", "monthlyRent": "1,200", "preDeposit": " "})
    assert (t.deposit_manwon, t.monthly_rent_manwon, t.pre_deposit_manwon) == (143500, 1200, None)


def test_rent_keys_are_order_independent_and_unique():
    typed = [to_typed_rent(i) for i in ITEMS]
    keyed = assign_rent_keys(typed)
    ids = [k.rent_id for k in keyed]
    assert len(set(ids)) == len(ids) == 161
    assert all(re.fullmatch(r"r11110-202407-[0-9a-f]{16}-\d+", i) for i in ids)
    shuffled = typed[:]
    random.Random(7).shuffle(shuffled)
    assert {(k.rent_id, k.attr_hash) for k in assign_rent_keys(shuffled)} == {(k.rent_id, k.attr_hash) for k in keyed}
    # 같은 지문 계약이 매매보다 흔하다(동 항목이 없음) — 이 파티션은 9개 그룹
    assert sum(1 for k in keyed if k.dup_seq == 1) == 9


# 속성 기반 (매매 test_fingerprint 와 같은 성질): 실제 응답 행을 바탕으로 지문 항목(보증금·월세·층)과 바뀔 수 있는 항목
# (계약 구분·갱신요구권·종전 보증금)을 좁은 값에서 뽑아, 같은 지문 그룹과 그룹 안 속성 차이가 자주 생기게 한다
TYPED = [to_typed_rent(i) for i in ITEMS]
rent_strategy = st.builds(
    lambda base, **kw: dataclasses.replace(base, **kw),
    st.sampled_from(TYPED[:5]),
    deposit_manwon=st.sampled_from([30000, 31000]),
    monthly_rent_manwon=st.sampled_from([0, 50]),
    floor=st.sampled_from([1, 2]),
    contract_type=st.sampled_from(["신규", "갱신", None]),
    use_rr_right=st.sampled_from(["사용", None]),
    pre_deposit_manwon=st.sampled_from([None, 28000, -19870]),
)


def _rent_signature(keyed):
    return sorted((k.rent_key, k.dup_seq, k.attr_hash) for k in keyed)


@settings(max_examples=200, deadline=None)
@given(st.lists(rent_strategy, min_size=0, max_size=12), st.randoms())
def test_rent_dup_seq_is_independent_of_input_order(rents, rnd):
    """같은 수집 결과를 어떤 순서로 받아도 (rent_key, dup_seq, attr_hash) 집합이 같다 — 공개 ID(rent_id)도 같다."""
    shuffled = rents[:]
    rnd.shuffle(shuffled)
    a, b = assign_rent_keys(rents), assign_rent_keys(shuffled)
    assert _rent_signature(a) == _rent_signature(b)
    assert sorted(k.rent_id for k in a) == sorted(k.rent_id for k in b)


@settings(max_examples=200, deadline=None)
@given(st.lists(rent_strategy, min_size=1, max_size=12))
def test_rent_keys_unique_and_dup_seq_numbers_each_fingerprint_group(rents):
    """현재행 키 (rent_key, dup_seq)·rent_id 가 파티션 안에서 유일하고, 같은 지문 그룹은 0..n-1 순번을
    속성 해시 순서대로 받는다 (그룹 크기 = 그 지문의 입력 수)."""
    keyed = assign_rent_keys(rents)
    assert len(keyed) == len(rents)
    assert len({(k.rent_key, k.dup_seq) for k in keyed}) == len({k.rent_id for k in keyed}) == len(rents)
    groups: dict[str, list] = {}
    for k in keyed:
        groups.setdefault(k.rent_key, []).append(k)
    assert Counter(rent_key(t) for t in rents) == {rk: len(g) for rk, g in groups.items()}
    for g in groups.values():
        assert sorted(k.dup_seq for k in g) == list(range(len(g)))
        by_seq = sorted(g, key=lambda k: k.dup_seq)
        assert [k.attr_hash for k in by_seq] == sorted(k.attr_hash for k in g)


def test_mutable_fields_change_attr_hash_not_key():
    t = to_typed_rent(ITEMS[0])
    for f in RENT_MUTABLE_FIELDS:
        changed = dataclasses.replace(t, **{f: "x" if isinstance(getattr(t, f), str) or getattr(t, f) is None else 1})
        assert rent_key(changed) == rent_key(t) and rent_attr_hash(changed) != rent_attr_hash(t), f
    for f, v in (("deposit_manwon", t.deposit_manwon + 1), ("monthly_rent_manwon", 7), ("floor", 99)):
        assert rent_key(dataclasses.replace(t, **{f: v})) != rent_key(t)
    # 보강 항목(단지 일련번호·도로명)은 지문에도 속성 해시에도 없다
    other = dataclasses.replace(t, apt_seq="x", road_nm="y")
    assert (rent_key(other), rent_attr_hash(other)) == (rent_key(t), rent_attr_hash(t))


def test_rent_and_trade_share_complex_keys():
    """같은 단지 일련번호(aptSeq)의 매매·전월세 행은 같은 단지 키 — 전세가율을 단지 키로 잇는 근거 (탐침 129/129)."""
    trade = parse_page((FIX / "rtms_dev_11110_202407.xml").read_text()).items
    by_seq = {r["aptSeq"]: complex_key(to_typed(r)) for r in trade}
    rents = {r["aptSeq"]: complex_key(to_typed_rent(r)) for r in ITEMS}
    common = set(by_seq) & set(rents)
    assert len(common) == 13 and all(by_seq[s] == rents[s] for s in common)


def test_validate_rent_partition():
    checks = {c.name: c for c in validate(_result(XML), 161, spec=RENT)}
    assert all(c.passed for c in checks.values()), checks
    assert checks["bronze_schema_matches_v1"].metric["schema_ver"] == 1
    bad = XML.replace("<aptSeq>", "<cdealType>O</cdealType><aptSeq>", 1)  # 원천이 항목을 더하면 격리
    check = {c.name: c for c in validate(_result(bad), 161, spec=RENT)}["bronze_schema_matches_v1"]
    assert not check.passed and check.metric["unknown_fields"] == ["cdealType"]
    assert row_matches_schema(ITEMS[0], 1, RENT) and not row_matches_schema(ITEMS[0], 1)  # 매매 v1 항목과는 다르다


def test_validate_blocks_rent_with_only_umd_name_changed():
    """이름만 바뀐 같은 계약(행정구역 개칭)은 매매처럼 수집 검사가 막는다 (ADR-042)."""
    umd = Counter(i["umdNm"] for i in ITEMS).most_common(1)[0][0]
    renamed = XML.replace(f"<umdNm>{umd}</umdNm>", "<umdNm>새이름동</umdNm>")
    check = {c.name: c for c in validate(_result(renamed), 161, prev_items=ITEMS, spec=RENT)}["umd_name_stable"]
    assert not check.passed and check.blocking and check.metric["sample"][0] == [umd, "새이름동"]


def test_rent_merge_uses_rent_tables_and_lines_up_branches():
    sql = merge_sql("202407", "('11110', 'iid', TIMESTAMP '2024-08-01 00:00:00.000000 UTC', 1)", RENT_MERGE)
    assert "MERGE INTO lake.silver.apt_rent t" in sql and "FROM lake.stage.apt_rent st" in sql
    assert "apt_trade" not in sql and "ON t.rent_key = src.mk" in sql
    missing = sql.split("'MISSING', NULL, ", 1)[1].split("\n    FROM cur c", 1)[0]
    parts = re.findall(r"[cp]\.\w+|CAST\(NULL AS [^)]*\)\)?", missing)
    assert len(parts) == len(RENT_MERGE.stage_cols) and parts[0] == "c.rent_key"
    same = sql.split("WHEN MATCHED THEN UPDATE SET", 1)[1].split("WHEN NOT MATCHED", 1)[0]
    assert "apt_seq = coalesce(src.apt_seq, t.apt_seq)" in same and "road_nm = coalesce" in same
    # 새 버전도 보강 열(일련번호·도로명)은 stage 가 비면 이전 현재행 값을 잇는다 (매매와 같은 생성기)
    branch = sql.split("'NEWVER', c.version + 1, ", 1)[1].split("\n    FROM s JOIN cur c", 1)[0]
    newver = re.findall(r"coalesce\(s\.\w+, c\.\w+\)|s\.\w+", branch)
    assert newver == [
        f"coalesce(s.{c}, c.{c})" if c in ("apt_seq", "road_nm") else f"s.{c}" for c in RENT_MERGE.stage_cols
    ]
    assert "SELECT rent_key, dup_seq, sgg_cd, attr_hash, version, apt_seq, road_nm FROM lake.silver.apt_rent" in sql
