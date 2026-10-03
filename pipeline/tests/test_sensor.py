"""수집 센서의 실행 계획 (현재 동작 고정): 우선순위 순서, 우선순위별 예산 상한, 진행 중 실행, 발행 전용 실행, 격자 등록.

센서가 부르는 DB·Redis·Dagster 는 가짜로 바꾸고, 센서가 돌려준 실행 요청만 본다.
(dbt manifest 가 필요하다: CI 는 dbt parse 뒤에 테스트를 돌린다)
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from dagster import DagsterInstance, RunRequest, SkipReason, build_sensor_context

from aptlake_pipeline import definitions as d

TODAY = dt.date(2026, 10, 3)
KEYS = [f"{y}{m:02d}" for y in (2025, 2026) for m in range(1, 13)][:23]  # 202501 ~ 202611
LEAF = ["11110", "11140", "41135"]


class FakeConn:
    def __init__(self, due):
        self.due = due
        self.sql: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        return SimpleNamespace(fetchall=lambda: self.due)


@pytest.fixture
def world(monkeypatch):
    """센서가 보는 바깥 상태. 테스트에서 값을 바꾼 뒤 run() 으로 평가한다."""
    w = SimpleNamespace(
        leaf=list(LEAF), due=[], active=[], used=0, cap=20, recheck_pending=False, publish=[], grid_calls=0, conn=None
    )

    @contextmanager
    def conn():
        w.conn = FakeConn(w.due)
        yield w.conn

    def ensure_grid(keys, leaf):
        w.grid_calls += 1

    monkeypatch.setattr(d.ops_db, "leaf_regions", lambda: w.leaf)
    monkeypatch.setattr(d.ops_db, "conn", conn)
    monkeypatch.setattr(d.ops_db, "ensure_grid", ensure_grid)
    monkeypatch.setattr(d.ops_db, "months_needing_publish", lambda: w.publish)
    monkeypatch.setattr(d, "kst_today", lambda: TODAY)
    monkeypatch.setattr(d, "monthly", SimpleNamespace(get_partition_keys=lambda: list(KEYS)))
    monkeypatch.setattr(d, "_budget", lambda: SimpleNamespace(snapshot=lambda: {"used": w.used}))
    monkeypatch.setattr(d, "_recheck_pending", lambda: w.recheck_pending)
    monkeypatch.setattr(d, "settings", lambda: SimpleNamespace(rtms_daily_cap=w.cap))
    instance = DagsterInstance.ephemeral()
    monkeypatch.setattr(instance, "get_runs", lambda filters=None, **_: w.active)

    def run(cursor=None):
        ctx = build_sensor_context(instance=instance, cursor=cursor)
        out = d.due_partitions_sensor(ctx)
        w.cursor = ctx.cursor
        return out

    w.run = run
    return w


def plan(out) -> list[tuple]:
    assert isinstance(out, list) and all(isinstance(r, RunRequest) for r in out)
    rows = []
    for r in out:
        cfg = r.run_config["ops"]["bronze__rtms_raw"]["config"]
        rows.append(
            (r.partition_key, r.tags["aptlake/priority"], tuple(cfg.get("sgg_codes", ())), cfg.get("fetch", True))
        )
    return rows


def due(ym, status, sgg):
    return {"deal_ym": ym, "status": status, "sgg": list(sgg)}


def test_priority_order_and_per_priority_ceilings(world):
    world.due = [
        due("202509", "PENDING", LEAF),  # 13개월 전 → 백필
        due("202601", "MERGED", LEAF),  # 9개월 전 → 재확인
        due("202604", "RETRY", ["41135"]),  # 6개월 전 재시도 → 재확인보다 한 단계 앞
        due("202609", "MERGED", LEAF),  # 증분
        due("202610", "PENDING", LEAF),  # 증분 (최신이 먼저)
    ]
    world.publish = ["202603", "202610"]
    # 상한(시군구 3개): 증분 20, 재확인 20-9=11, 백필 11 → 202509(3건)은 10+3>11 이라 이번 틱에서 빠진다
    assert plan(world.run()) == [
        ("202610", "incremental", tuple(LEAF), True),
        ("202609", "incremental", tuple(LEAF), True),
        ("202604", "retry", ("41135",), True),
        ("202601", "recheck", tuple(LEAF), True),
        ("202603", "publish-only", (), False),  # 이미 요청한 202610 은 발행 전용을 따로 만들지 않는다
    ]
    # 범위 밖 달·현존하지 않는 시군구는 조회에서부터 뺀다 (ADR-033)
    sql, params = world.conn.sql[0]
    assert params == (KEYS[0], KEYS[-1], LEAF) and "BETWEEN" in sql


def test_recheck_reserve_shrinks_backfill_ceiling(world):
    world.due = [due("202509", "PENDING", LEAF)]
    world.recheck_pending = True  # 재확인 몫(3×12=36)을 남겨 두므로 백필 상한 = max(20-9-36, 0) = 0
    out = world.run()
    assert isinstance(out, SkipReason) and "nothing due within budget" in str(out.skip_message)
    world.recheck_pending = False
    assert plan(world.run()) == [("202509", "backfill", tuple(LEAF), True)]


def test_in_flight_months_are_skipped_and_their_calls_counted(world):
    queued = SimpleNamespace(
        tags={"dagster/partition": "202610"},
        run_config={"ops": {"bronze__rtms_raw": {"config": {"sgg_codes": ["11110", "11140"]}}}},
    )
    publish_only = SimpleNamespace(
        tags={"dagster/partition": "202603"},
        run_config={"ops": {"bronze__rtms_raw": {"config": {"fetch": False}}}},
    )
    world.active = [queued, publish_only]
    world.used = 7
    world.due = [due("202610", "PENDING", LEAF), due("202609", "MERGED", LEAF), due("202601", "MERGED", LEAF)]
    world.publish = ["202603"]
    # 사용 7 + 대기 중 실행 2건(발행 전용은 0) = 9 → 202609(3) → 12, 202601 재확인 12+3 > 11 → 제외, 202603 은 진행 중
    assert plan(world.run()) == [("202609", "incremental", tuple(LEAF), True)]


def test_no_regions_or_nothing_due(world):
    world.leaf = []
    assert isinstance(world.run(), SkipReason)
    world.leaf = list(LEAF)
    out = world.run()
    assert isinstance(out, SkipReason) and str(out.skip_message).startswith("nothing due within budget (used 0")


def test_grid_registered_only_when_shape_changes(world):
    world.run()
    first = world.cursor
    world.run(cursor=first)
    assert world.grid_calls == 1  # 같은 모양이면 1.8만 행 등록을 되풀이하지 않는다
    world.leaf = [*LEAF, "41131"]
    world.run(cursor=first)
    assert world.grid_calls == 2 and world.cursor != first
