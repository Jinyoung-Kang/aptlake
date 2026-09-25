import dataclasses
import datetime as dt
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from aptlake_pipeline.rtms.fingerprint import (
    assign_keys,
    attr_hash,
    complex_key,
    duplicate_groups,
    norm_jibun,
    norm_text,
    trade_key,
)
from aptlake_pipeline.rtms.parse import TypedTrade, parse_page, to_typed

FIX = Path(__file__).parent / "fixtures"


def make(**kw) -> TypedTrade:
    base = dict(
        sgg_cd="11110",
        umd_nm="숭인동",
        apt_nm="종로중흥S클래스",
        jibun="202-3",
        area_m2=Decimal("17.8110"),
        deal_date=dt.date(2024, 7, 23),
        price_manwon=12000,
        floor=10,
        build_year=2013,
        is_cancelled=False,
        cancel_date=None,
        registered_date=None,
        apt_dong=None,
        deal_kind="중개거래",
        agent_sgg_nm="서울 종로구",
        seller_type="개인",
        buyer_type="개인",
        land_leasehold=False,
    )
    base.update(kw)
    return TypedTrade(**base)


def test_normalization_absorbs_notation_differences():
    assert norm_text("종로 중흥S클래스") == norm_text("종로중흥Ｓ클래스") == "종로중흥s클래스"
    assert norm_jibun("0202-0003") == norm_jibun(" 202 - 3 ") == "202-3"
    assert norm_jibun("산 12") == "산12"


def test_complex_key_merges_notation_variants():  # FR-205
    a = make(apt_nm="종로 중흥S클래스", jibun="0202-0003")
    b = make(apt_nm="종로중흥Ｓ클래스", jibun="202-3")
    assert complex_key(a) == complex_key(b)
    assert complex_key(a) != complex_key(make(apt_nm="다른단지"))


def test_mutable_fields_do_not_change_trade_key():
    a = make()
    b = make(is_cancelled=True, cancel_date=dt.date(2024, 9, 1), registered_date=dt.date(2024, 9, 30), apt_dong="101")
    assert trade_key(a) == trade_key(b)
    assert attr_hash(a) != attr_hash(b)


def test_immutable_fields_change_trade_key():
    a = make()
    for change in [
        dict(price_manwon=12001),
        dict(floor=11),
        dict(area_m2=Decimal("17.8111")),
        dict(deal_date=dt.date(2024, 7, 24)),
    ]:
        assert trade_key(a) != trade_key(make(**change))


def test_identical_trades_get_distinct_dup_seq():
    keyed = assign_keys([make(), make(), make(floor=3)])
    same = sorted(k.dup_seq for k in keyed if k.trade.floor == 10)
    assert same == [0, 1]
    assert len({k.trade_id for k in keyed}) == 3
    assert duplicate_groups(keyed) == 1


def _signature(keyed):
    return sorted((k.trade_key, k.dup_seq, k.attr_hash) for k in keyed)


trade_strategy = st.builds(
    make,
    price_manwon=st.sampled_from([10000, 12000]),
    floor=st.sampled_from([1, 2]),
    is_cancelled=st.booleans(),
    apt_dong=st.sampled_from([None, "101", "102"]),
)


@settings(max_examples=200, deadline=None)
@given(st.lists(trade_strategy, min_size=0, max_size=12), st.randoms())
def test_dup_seq_is_independent_of_input_order(trades, rnd):
    """속성 기반: 같은 수집 결과를 어떤 순서로 받아도 (trade_key, dup_seq, attr_hash) 집합이 같다."""
    shuffled = trades[:]
    rnd.shuffle(shuffled)
    assert _signature(assign_keys(trades)) == _signature(assign_keys(shuffled))


@settings(max_examples=100, deadline=None)
@given(st.lists(trade_strategy, min_size=1, max_size=12))
def test_keys_are_unique_within_partition(trades):
    keyed = assign_keys(trades)
    assert len({(k.trade_key, k.dup_seq) for k in keyed}) == len(trades)


def test_real_fixture_keys_unique_and_stable():
    items = parse_page((FIX / "rtms_11110_202407.xml").read_text()).items
    typed = [to_typed(i) for i in items]
    a, b = assign_keys(typed), assign_keys(list(reversed(typed)))
    assert _signature(a) == _signature(b)
    assert len({k.trade_id for k in a}) == len(items)


def test_trade_id_embeds_partition():
    k = assign_keys([make()])[0]
    assert k.trade_id.startswith("11110-202407-") and k.trade_id.endswith("-0")


def test_dataclass_is_frozen():
    t = make()
    try:
        t.floor = 3  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("TypedTrade must be immutable")
