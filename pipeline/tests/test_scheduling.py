"""수집 실행 계획(순수 함수) 단위 테스트 — Dagster·DB 없이 규칙만."""

from __future__ import annotations

import datetime as dt

import pytest

from aptlake_pipeline.budget import Ceilings, ceilings
from aptlake_pipeline.scheduling import Planned, plan_runs, priority_for, run_calls

TODAY = dt.date(2026, 10, 3)
LEAF = ("11110", "11140", "41135")


def due(ym: str, status: str, sgg=LEAF) -> dict:
    return {"deal_ym": ym, "status": status, "sgg": list(sgg)}


def plan(rows, *, used=0, queued=0, ceil=None, in_flight=(), publish=(), keys=None):
    return plan_runs(
        rows,
        today=TODAY,
        in_flight=set(in_flight),
        used=used,
        queued_calls=queued,
        ceilings=ceil or Ceilings(incremental=100, recheck=100, backfill=100),
        needing_publish=list(publish),
        partition_keys=keys or [r["deal_ym"] for r in rows] + list(publish),
    )


@pytest.mark.parametrize(
    ("ym", "prio"),
    [("202610", "incremental"), ("202608", "incremental"), ("202607", "recheck"), ("202511", "recheck"),
     ("202510", "backfill"), ("200601", "backfill")],
)  # fmt: skip
def test_priority_by_age_in_months(ym, prio):
    assert priority_for(ym, TODAY) == prio


def test_order_retry_moves_one_step_and_newest_first():
    out = plan([due("202509", "PENDING"), due("202601", "MERGED"), due("202509", "RETRY", ["11110"]),
                due("202602", "MERGED"), due("202610", "PENDING")])  # fmt: skip
    # 재시도가 섞인 202509(백필)는 재확인과 같은 칸으로 — 같은 칸이면 최신 달이 먼저
    assert [(p.ym, p.priority) for p in out] == [
        ("202610", "incremental"),
        ("202602", "recheck"),
        ("202601", "recheck"),
        ("202509", "retry"),
    ]
    assert out[-1].sgg_codes == LEAF  # 같은 달의 상태별 행은 시군구를 합친다


def test_budget_skips_month_that_does_not_fit_but_later_smaller_month_can():
    c = Ceilings(incremental=10, recheck=10, backfill=10)
    out = plan([due("202610", "PENDING"), due("202609", "PENDING"), due("202608", "PENDING", ["11110"])],
               used=5, ceil=c)  # fmt: skip
    # 5 + 3 = 8, 8 + 3 > 10 → 202609 건너뜀, 8 + 1 = 9 → 202608 들어감 (상한과 같으면 들어간다)
    assert [p.ym for p in out] == ["202610", "202608"]


def test_queued_calls_and_in_flight_months():
    out = plan([due("202610", "PENDING"), due("202609", "PENDING")], queued=8, ceil=Ceilings(10, 10, 10),
               in_flight={"202610"})  # fmt: skip
    assert out == []  # 202610 은 진행 중, 202609 는 8 + 3 > 10


def test_publish_only_months_without_fetch():
    out = plan([due("202610", "PENDING")], publish=["202610", "202603", "209901"], in_flight={"202603"},
               keys=["202610", "202603", "202602"])  # fmt: skip
    assert out == [Planned("202610", "incremental", LEAF)]  # 요청한 달·진행 중인 달·범위 밖 달은 발행 전용을 안 만든다
    out = plan([], publish=["202602"], keys=["202602"])
    assert out == [Planned("202602", "publish-only", (), fetch=False)]


def test_run_calls_of_queued_runs():
    cfg = lambda c: {"ops": {"bronze__rtms_raw": {"config": c}}}  # noqa: E731
    assert run_calls(cfg({"sgg_codes": ["11110", "11140"]}), 250) == 2
    assert run_calls(cfg({"fetch": False}), 250) == 0  # 발행 전용
    assert run_calls(cfg({}), 250) == 250  # 시군구 목록 없음 = 전체
    assert run_calls({}, 250) == 250


def test_reserves_shrink_lower_priority_ceilings():
    c = ceilings(cap=8000, n_regions=250, recheck_pending=True)
    assert (c.incremental, c.recheck, c.backfill) == (8000, 8000 - 750, 8000 - 750 - 3000)
