import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from aptlake_pipeline import budget
from aptlake_pipeline.budget import Budget, ceilings


def test_priority_ceilings_reserve_room_for_higher_priorities():
    c = ceilings(cap=8000, n_regions=256, recheck_pending=True)
    assert c.incremental == 8000
    assert c.recheck == 8000 - 256 * 3
    assert c.backfill == 8000 - 256 * 3 - 256 * 12
    assert c.backfill < c.recheck < c.incremental


def test_no_recheck_reserve_when_nothing_due():
    c = ceilings(cap=8000, n_regions=256, recheck_pending=False)
    assert c.backfill == c.recheck == 8000 - 768


def test_ceiling_never_negative():
    c = ceilings(cap=100, n_regions=256, recheck_pending=True)
    assert c.backfill == 0 and c.recheck == 0 and c.incremental == 100


# ───── Redis 가 비었을 때(재시작·만료) 누적값 복원 ─────


class FakeRedis:
    """Budget 이 쓰는 명령만 흉내 낸다 (값은 실제 클라이언트처럼 bytes)."""

    def __init__(self):
        self.h: dict[str, dict[bytes, bytes]] = {}
        self.ttl: dict[str, int] = {}

    def register_script(self, _src):
        return lambda keys, args: pytest.fail("snapshot 은 차감 스크립트를 부르지 않는다")

    def exists(self, key):
        return int(key in self.h)

    def hgetall(self, key):
        return dict(self.h.get(key, {}))

    def hsetnx(self, key, field, value):
        self.h.setdefault(key, {}).setdefault(field.encode(), str(value).encode())

    def expire(self, key, seconds):
        self.ttl[key] = seconds

    def pipeline(self):
        r, ops = self, []

        class Pipe:
            def __getattr__(self, name):
                return lambda *a: ops.append((name, a))

            def execute(self):
                for name, a in ops:
                    getattr(r, name)(*a)

        return Pipe()


DAY = dt.date(2026, 10, 21)


@pytest.fixture
def pg(monkeypatch):
    """ops.api_budget 한 행 (None 이면 기록 없음). 조회 횟수를 센다."""
    state = SimpleNamespace(row=None, reads=0)

    @contextmanager
    def connect(_dsn):
        def execute(sql, params):
            assert "FROM ops.api_budget" in sql and params == (DAY, "rent")
            state.reads += 1
            return SimpleNamespace(fetchone=lambda: state.row)

        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(budget.psycopg, "connect", connect)
    return state


def test_snapshot_restores_used_from_pg_when_redis_key_is_gone(pg):
    """Redis 재시작 직후: 빈 해시를 사용량 0 으로 읽으면 센서가 하루 몫을 넘게 계획한다 → PG 기록에서 먼저 되살린다."""
    pg.row = (7232, {"backfill": 7232})
    r = FakeRedis()
    b = Budget(r, "dsn", "rent", daily_limit=10000, cap=8000)
    assert b.snapshot(DAY) == {"used": 7232, "p:backfill": 7232}
    key = f"budget:rent:{DAY.isoformat()}"
    assert r.h[key] == {b"used": b"7232", b"p:backfill": b"7232"} and r.ttl[key] == 3 * 86400
    assert b.snapshot(DAY)["used"] == 7232 and pg.reads == 1  # 복원한 뒤로는 Redis 만 읽는다


def test_snapshot_keeps_live_redis_value_without_reading_pg(pg):
    r = FakeRedis()
    r.h[f"budget:rent:{DAY.isoformat()}"] = {b"used": b"120", b"p:incremental": b"120", b"exhausted_by_source": b"x"}
    assert Budget(r, "dsn", "rent", 10000, 8000).snapshot(DAY) == {"used": 120, "p:incremental": 120}
    assert pg.reads == 0


def test_snapshot_without_any_record_starts_at_zero(pg):
    r = FakeRedis()
    assert Budget(r, "dsn", "rent", 10000, 8000).snapshot(DAY) == {"used": 0}
    assert pg.reads == 1
