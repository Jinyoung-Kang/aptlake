"""변경분 피드 비교 규칙 (ADR-045, 명세 2.4)."""

from __future__ import annotations

import datetime as dt

from aptlake_pipeline.changes import FIELDS, diff_month

T = dt.datetime(2026, 9, 2, tzinfo=dt.UTC)


def r(tid: str, **kw) -> dict:
    base = {c: None for c in FIELDS} | {"trade_id": tid, "is_cancelled": 0, "version": 1, "price_manwon": 50000,
                                        "is_outlier": 0, "valid_from": T}  # fmt: skip
    return base | kw


def kinds(changes) -> list[tuple]:
    return [(c.trade_id, c.change, c.changed_fields) for c in changes]


def test_five_kinds_and_unchanged_rows():
    old = [r("a"), r("b"), r("c"), r("d", missing_since=T), r("same")]
    new = [
        r("b", is_cancelled=1, cancel_date=dt.date(2026, 9, 30), version=2, valid_from=T + dt.timedelta(days=3)),
        r("c", missing_since=T),
        r("d"),
        r("e"),
        r("same"),
    ]
    assert kinds(diff_month(old, new)) == [
        ("a", "removed", ()),
        ("b", "updated", ("cancelled", "cancelDate", "version")),
        ("c", "missing", ()),
        ("d", "restored", ()),
        ("e", "added", ()),
    ]


def test_removed_carries_last_known_row_and_others_the_new_row():
    out = {c.trade_id: c for c in diff_month([r("a", price_manwon=1)], [r("b", price_manwon=2)])}
    assert out["a"].row["price_manwon"] == 1 and out["b"].row["price_manwon"] == 2


def test_missing_together_with_other_change_is_updated_with_both_fields():
    out = diff_month([r("a")], [r("a", missing_since=T, is_outlier=1)])
    assert kinds(out) == [("a", "updated", ("outlier", "missingSince"))]


def test_valid_from_alone_is_not_a_change_and_no_previous_means_all_added():
    assert diff_month([r("a")], [r("a", valid_from=T + dt.timedelta(days=1))]) == []
    assert [c.change for c in diff_month([], [r("a"), r("b")])] == ["added", "added"]


def apply(rows: list[dict], changes) -> list[dict]:
    """고객 쪽 동기화: 받은 변경을 순서대로 적용 (removed 는 지우고, 나머지는 새 값으로 덮는다)."""
    table = {x["trade_id"]: x for x in rows}
    for c in changes:
        if c.change == "removed":
            table.pop(c.trade_id, None)
        else:
            table[c.trade_id] = dict(c.row)
    return sorted(table.values(), key=lambda x: x["trade_id"])


def test_snapshot_plus_feed_equals_latest_serving():
    """명세 2.7-2: 버전 V 스냅숏 + V 이후 변경을 차례로 적용 = 최신 서빙 (여러 발행에 걸쳐)."""
    v0 = [r("a"), r("b"), r("c", missing_since=T)]
    v1 = [r("a", is_outlier=1), r("c"), r("d")]  # b 사라짐(ID 변경), c 복귀, d 추가
    v2 = [r("a", is_outlier=1, is_cancelled=1, version=2), r("c", missing_since=T), r("d"), r("e")]
    client = apply(apply(list(v0), diff_month(v0, v1)), diff_month(v1, v2))
    assert client == sorted(v2, key=lambda x: x["trade_id"])
