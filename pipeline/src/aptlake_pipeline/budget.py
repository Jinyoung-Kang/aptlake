"""일일 호출 예산 (FR-103).

- 하루 한도(공공데이터포털 개발계정 10,000)의 RTMS_BUDGET_PCT(기본 80%)가 상한.
- 우선순위: 증분(incremental) > 재확인(recheck) > 백필(backfill).
  하위 우선순위는 상위 몫으로 남겨 둔 호출을 쓰지 못한다 (priority ceiling).
- 차감은 Redis Lua 스크립트로 원자적으로 한다 → 동시 실행 중에도 상한 초과 0.
- 날짜 경계는 한국 시간 자정 (포털 한도 초기화 기준).
- Redis 값은 ops.api_budget 에 영속화하고, Redis 가 비어 있으면 PG 값에서 복원한다.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import psycopg
import redis

KST = ZoneInfo("Asia/Seoul")
PRIORITIES = ("incremental", "recheck", "backfill")

# KEYS[1] = budget:{source}:{day}   (hash: used, p:<priority>)
# ARGV    = n, ceiling, priority, ttl_seconds, restore_used (복원은 호출 전에 HSETNX 로 하므로 보통 0)
_RESERVE = """
local used = tonumber(redis.call('HGET', KEYS[1], 'used') or '-1')
if used < 0 then
  used = tonumber(ARGV[5])
  redis.call('HSET', KEYS[1], 'used', used)
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
end
local n = tonumber(ARGV[1])
if used + n > tonumber(ARGV[2]) then
  return -1
end
redis.call('HINCRBY', KEYS[1], 'p:' .. ARGV[3], n)
return redis.call('HINCRBY', KEYS[1], 'used', n)
"""


class BudgetExhausted(Exception):
    pass


@dataclass(frozen=True)
class Ceilings:
    """우선순위별 사용 가능 상한 (그날 누적 기준)."""

    incremental: int
    recheck: int
    backfill: int

    def for_priority(self, p: str) -> int:
        return getattr(self, p)


def ceilings(cap: int, n_regions: int, recheck_pending: bool) -> Ceilings:
    """증분 몫 = 시군구 × 최근 3개월, 재확인 몫 = 시군구 × 12개월(재확인 대기 중일 때만)."""
    inc_reserve = n_regions * 3
    rc_reserve = n_regions * 12 if recheck_pending else 0
    return Ceilings(
        incremental=cap,
        recheck=max(cap - inc_reserve, 0),
        backfill=max(cap - inc_reserve - rc_reserve, 0),
    )


def kst_today(now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(tz=KST)).astimezone(KST).date()


class Budget:
    def __init__(self, r: redis.Redis, pg_dsn: str, source: str, daily_limit: int, cap: int):
        self.r, self.pg_dsn, self.source = r, pg_dsn, source
        self.daily_limit, self.cap = daily_limit, cap
        self._reserve = r.register_script(_RESERVE)

    def _key(self, day: dt.date) -> str:
        return f"budget:{self.source}:{day.isoformat()}"

    def _restore_from_pg(self, day: dt.date) -> None:
        """Redis 키가 없으면(재시작·만료) PG 기록으로 누적값·우선순위별 값을 되살린다. HSETNX 라 동시 복원도 안전."""
        with psycopg.connect(self.pg_dsn) as c:
            row = c.execute(
                "SELECT used_calls, by_priority FROM ops.api_budget WHERE day=%s AND source=%s", (day, self.source)
            ).fetchone()
        key = self._key(day)
        pipe = self.r.pipeline()
        pipe.hsetnx(key, "used", int(row[0]) if row else 0)
        for prio, n in ((row[1] or {}) if row else {}).items():
            pipe.hsetnx(key, f"p:{prio}", int(n))
        pipe.expire(key, 3 * 86400)
        pipe.execute()

    def reserve(self, n: int, priority: str, ceiling: int, day: dt.date | None = None) -> int:
        """n 회 호출 몫을 차감한다. 상한을 넘으면 BudgetExhausted. 반환: 차감 후 누적 사용량."""
        if priority not in PRIORITIES:
            raise ValueError(priority)
        day = day or kst_today()
        if not self.r.exists(self._key(day)):
            self._restore_from_pg(day)
        used = self._reserve(keys=[self._key(day)], args=[n, min(ceiling, self.cap), priority, 3 * 86400, 0])
        if int(used) < 0:
            raise BudgetExhausted(f"{self.source} {priority} ceiling {ceiling} reached on {day}")
        return int(used)

    def mark_exhausted(self, reason: str, day: dt.date | None = None) -> None:
        """원천이 한도 초과를 알리면(다른 프로그램이 같은 키를 쓰는 경우 등) 그날 남은 예산을 0 으로 만든다.
        이후 reserve 는 모두 BudgetExhausted → 센서도 다음 날(KST)까지 수집 실행을 만들지 않는다."""
        day = day or kst_today()
        key = self._key(day)
        if not self.r.exists(key):
            self._restore_from_pg(day)
        used = int(self.r.hget(key, "used") or 0)
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"used": max(used, self.cap), "exhausted_by_source": reason[:120]})
        pipe.expire(key, 3 * 86400)
        pipe.execute()

    def snapshot(self, day: dt.date | None = None) -> dict[str, int]:
        day = day or kst_today()
        raw = self.r.hgetall(self._key(day))
        out: dict[str, int] = {}
        for k, v in raw.items():
            name = k.decode() if isinstance(k, bytes) else k
            if name.startswith("exhausted"):
                continue
            out[name] = int(v)
        return out

    def persist(self, day: dt.date | None = None) -> None:
        """Redis 누적값을 ops.api_budget 에 기록 (GREATEST 로 역행 방지)."""
        day = day or kst_today()
        snap = self.snapshot(day)
        if not snap:
            return
        by_p = {k[2:]: v for k, v in snap.items() if k.startswith("p:")}
        with psycopg.connect(self.pg_dsn) as c:
            c.execute(
                """INSERT INTO ops.api_budget (day, source, limit_calls, used_calls, by_priority)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (day, source) DO UPDATE
                   SET used_calls = GREATEST(ops.api_budget.used_calls, EXCLUDED.used_calls),
                       limit_calls = EXCLUDED.limit_calls,
                       -- 우선순위별 값도 키마다 큰 값 유지 (역행 방지)
                       by_priority = (SELECT coalesce(jsonb_object_agg(k, GREATEST(
                                          coalesce((ops.api_budget.by_priority->>k)::int, 0),
                                          coalesce((EXCLUDED.by_priority->>k)::int, 0))), '{}'::jsonb)
                                      FROM jsonb_object_keys(ops.api_budget.by_priority
                                                             || EXCLUDED.by_priority) AS k)""",
                (day, self.source, self.daily_limit, snap.get("used", 0), json.dumps(by_p)),
            )
