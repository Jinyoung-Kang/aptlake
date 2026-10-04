"""행정구역 승격·개칭으로 법정동 이름이 바뀔 때 같은 거래가 새 거래로 중복되지 않게 (대구 달성군 구지면 → 구지읍).

겪은 일: 행정안전부 법정동코드에 '구지읍'이 새 코드로 적용된 뒤, 실거래 원천은 그 지역 거래의 법정동 이름(umdNm)을
빈 값으로 보냈다(두 달 34행). 빈 값은 파싱 검사가 막았지만, 원천이 '구지읍 ○○리'로 채우면 지문 키(법정동 이름 포함)가
바뀌어 같은 거래가 새 거래로 들어가고 기존 거래는 '사라짐'이 된다 — 그때는 막는 검사가 없었다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

from aptlake_pipeline.ingest import validate
from aptlake_pipeline.regions import derive_umd_aliases, official_umd_names
from aptlake_pipeline.rtms.client import FetchResult
from aptlake_pipeline.rtms.fingerprint import assign_keys, norm_text
from aptlake_pipeline.rtms.parse import TypedTrade, parse_page

FIX = Path(__file__).parent / "fixtures"


def _trade(**kw) -> TypedTrade:
    base = dict(
        sgg_cd="27710",
        umd_nm="구지면 응암리",
        apt_nm="대구국가산단반도유보라아이비파크",
        jibun="900",
        area_m2=Decimal("84.9900"),
        deal_date=dt.date(2026, 8, 5),
        price_manwon=38000,
        floor=12,
        build_year=2019,
        is_cancelled=False,
        cancel_date=None,
        registered_date=None,
        apt_dong=None,
        deal_kind="중개거래",
        agent_sgg_nm="대구 달성군",
        seller_type="개인",
        buyer_type="개인",
        land_leasehold=False,
    )
    base.update(kw)
    return TypedTrade(**base)


def test_rename_with_alias_keeps_trade_id_and_complex_key():
    old = _trade()
    new = dataclasses.replace(old, umd_nm="구지읍 응암리")
    aliases = {("27710", norm_text("구지읍 응암리")): "구지면 응암리"}
    [a] = assign_keys([old])
    [b] = assign_keys([new], aliases)
    assert (a.trade_key, a.trade_id, a.complex_key) == (b.trade_key, b.trade_id, b.complex_key)


def test_without_alias_rename_changes_keys():
    """대응표가 없으면 이름만 바뀌어도 다른 거래가 된다 — 이 위험을 수집 검사(umd_name_stable)가 막는다."""
    old = _trade()
    [a] = assign_keys([old])
    [b] = assign_keys([dataclasses.replace(old, umd_nm="구지읍 응암리")])
    assert a.trade_key != b.trade_key


def _official_rows() -> list[dict]:
    def row(code: str, name: str) -> dict:
        return {"region_cd": code, "sido_cd": code[:2], "sgg_cd": code[2:5], "umd_cd": code[5:8], "ri_cd": code[8:],
                "locatadd_nm": name}  # fmt: skip

    return [
        row("2700000000", "대구광역시"),
        row("2771000000", "대구광역시 달성군"),
        row("2771026800", "대구광역시 달성군 구지읍"),
        row("2771026822", "대구광역시 달성군 구지읍 응암리"),
        row("2771026821", "대구광역시 달성군 구지읍 창리"),
        row("2771025622", "대구광역시 달성군 다사읍 매곡리"),
        row("2771038021", "대구광역시 달성군 가창면 대일리"),
        row("1111017500", "서울특별시 종로구 숭인동"),
        row("1111000000", "서울특별시 종로구"),
    ]


def test_official_names_strip_region_prefix():
    names = official_umd_names(_official_rows())
    assert names["27710"] >= {"구지읍", "구지읍 응암리", "다사읍 매곡리", "가창면 대일리"}
    assert "숭인동" in names["11110"]


def test_derive_aliases_only_for_myeon_to_eup_promotion_confirmed_by_official_list():
    observed = [
        ("27710", "구지면 응암리"),  # 공식 목록에 없고 '구지읍 응암리'가 있다 → 승격
        ("27710", "구지면 창리"),
        ("27710", "다사읍 매곡리"),  # 지금도 공식 이름
        ("27710", "가창면 대일리"),  # 면이지만 지금도 공식 이름 → 대응 안 함
        ("27710", "옥포면 기세리"),  # 공식 목록에 '옥포읍 기세리'도 없다 → 추측하지 않음
        ("11110", "숭인동"),
    ]
    got = derive_umd_aliases(official_umd_names(_official_rows()), observed)
    assert got == [("27710", "구지읍 응암리", "구지면 응암리"), ("27710", "구지읍 창리", "구지면 창리")]


def _result(xml: str) -> FetchResult:
    return FetchResult("11110", "202407", [xml.encode()], [parse_page(xml)], 0.0, 1)


def test_validate_blocks_same_trade_with_changed_umd_name():
    prev_xml = (FIX / "rtms_11110_202407.xml").read_text()
    prev = parse_page(prev_xml).items
    renamed = _result(prev_xml.replace("<umdNm>숭인동</umdNm>", "<umdNm>숭인1동</umdNm>"))
    checks = {c.name: c for c in validate(renamed, len(prev), prev_items=prev)}
    c = checks["umd_name_stable"]
    assert c.blocking and not c.passed
    assert c.metric["renamed"] == 15 and c.metric["sample"][0] == ["숭인동", "숭인1동"]


def test_validate_passes_rename_covered_by_alias_and_unchanged_payload():
    prev_xml = (FIX / "rtms_11110_202407.xml").read_text()
    prev = parse_page(prev_xml).items
    renamed = _result(prev_xml.replace("<umdNm>숭인동</umdNm>", "<umdNm>숭인1동</umdNm>"))
    aliases = {("11110", norm_text("숭인1동")): "숭인동"}
    assert {c.name: c for c in validate(renamed, len(prev), prev_items=prev, aliases=aliases)}["umd_name_stable"].passed
    same = {c.name: c for c in validate(_result(prev_xml), len(prev), prev_items=prev)}
    assert same["umd_name_stable"].passed
    first = {c.name: c for c in validate(_result(prev_xml), None)}  # 처음 받는 파티션은 비교 대상이 없다
    assert first["umd_name_stable"].passed
