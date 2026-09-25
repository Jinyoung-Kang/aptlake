"""인증·인가·한도 (FR-501, 기획서 그림 3).

요청마다:
  1) X-API-Key 파싱 → key_id 로 Redis 캐시(30초) 또는 PostgreSQL 조회 → HMAC 상수시간 비교
     헤더가 없으면 anonymous 플랜 (클라이언트 IP 기준 — 신뢰 프록시에서 온 경우만 X-Forwarded-For 사용)
  2) 스코프 확인 (라우트마다 require_scope 필수, 없으면 앱 기동 실패 = 기본 거부)
  3) 분당 요청 한도 (슬라이딩 윈도우, Redis Lua 원자 실행) → 초과 시 429 + Retry-After
  4) 일일 행 한도는 응답 행 수로 차감 (charge_rows)
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any, cast

import orjson
from fastapi import Request

from . import keys
from .errors import ApiError
from .resources import Resources
from .settings import Settings, settings


@dataclass(frozen=True)
class Plan:
    plan_id: str
    rpm: int
    daily_rows: int
    max_range_months: int | None
    max_page_size: int
    allow_bulk: bool


@dataclass
class Principal:
    kind: str  # "key" | "anonymous"
    subject: str  # key_id 또는 'ip:<hmac>'
    client_id: str
    plan: Plan
    scopes: frozenset[str]
    limit_headers: dict[str, str] = field(default_factory=dict)
    rows_used_today: int = 0


ANON_SCOPES = frozenset({"read"})

# 요청 관문 (Redis 왕복 1회): 슬라이딩 윈도우 분당 한도 검사·증가 + 오늘 사용한 행 수 조회.
#   슬라이딩 윈도우 = 현재 분 카운트 + 이전 분 카운트 × (1 - 현재 분 경과 비율)
# KEYS[1]=현재 분 키, KEYS[2]=이전 분 키, KEYS[3]=오늘 행 사용량 키 / ARGV = limit, 경과 비율(0~1), ttl
_SLIDING = """
local cur = tonumber(redis.call('GET', KEYS[1]) or '0')
local prev = tonumber(redis.call('GET', KEYS[2]) or '0')
local rows = tonumber(redis.call('GET', KEYS[3]) or '0')
local est = prev * (1 - tonumber(ARGV[2])) + cur
if est + 1 > tonumber(ARGV[1]) then
  return {0, math.floor(est), rows}
end
cur = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
return {1, math.floor(prev * (1 - tonumber(ARGV[2])) + cur), rows}
"""

# 프로세스 내 키 캐시 (Redis 왕복 절약). 폐기 시 Redis 캐시는 즉시 지우고, 이 캐시는 최대 TTL 초 뒤 반영
_LOCAL_KEY_TTL_S = 5.0
_local_keys: dict[str, tuple[float, dict | None]] = {}


def client_ip(request: Request, s: Settings) -> str:
    peer = request.client.host if request.client else "0.0.0.0"  # noqa: S104
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if any(peer_ip in n for n in s.trusted_networks):
        xff = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        # 오른쪽부터 신뢰 프록시가 아닌 첫 주소 (클라이언트가 앞에 붙인 값은 무시)
        for hop in reversed(xff):
            try:
                ip = ipaddress.ip_address(hop)
            except ValueError:
                break
            if not any(ip in n for n in s.trusted_networks):
                return str(ip)
    return peer


async def load_plans(res: Resources) -> dict[str, Plan]:
    async with res.pg.connection() as c:
        rows = cast(list[dict[str, Any]], await (await c.execute("SELECT * FROM api.plan")).fetchall())
    return {r["plan_id"]: Plan(**r) for r in rows}


async def _lookup_key(res: Resources, key_id: str, s: Settings) -> dict | None:
    hit = _local_keys.get(key_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    data = await _lookup_key_shared(res, key_id, s)
    if len(_local_keys) > 10_000:
        _local_keys.clear()
    _local_keys[key_id] = (time.monotonic() + _LOCAL_KEY_TTL_S, data)
    return data


def forget_key(key_id: str) -> None:
    _local_keys.pop(key_id, None)


async def _lookup_key_shared(res: Resources, key_id: str, s: Settings) -> dict | None:
    cache_key = f"al:key:{key_id}"
    cached = await res.redis.get(cache_key)
    if cached is not None:
        return None if cached == "-" else orjson.loads(cached)
    async with res.pg.connection() as c:
        raw = await (
            await c.execute(
                """SELECT k.key_id, k.secret_hmac, k.scopes, k.expires_at, k.revoked_at, c.client_id::text AS client_id,
                      c.plan_id, c.status
               FROM api.api_key k JOIN api.client c USING (client_id) WHERE k.key_id = %s""",
                (key_id,),
            )
        ).fetchone()
    row = cast(dict[str, Any] | None, raw)  # dict_row 은 풀 설정
    if row is None:
        await res.redis.set(cache_key, "-", ex=s.key_cache_ttl_s)  # 없는 키도 짧게 캐시 (DB 두드리기 방지)
        return None
    data = {
        **row,
        "expires_at": row["expires_at"].isoformat(),
        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
    }
    await res.redis.set(cache_key, orjson.dumps(data), ex=s.key_cache_ttl_s)
    return data


async def resolve_principal(request: Request) -> Principal:
    s = settings()
    res: Resources = request.app.state.res
    plans: dict[str, Plan] = request.app.state.plans
    raw = request.headers.get("x-api-key")
    if raw is None:
        ip = client_ip(request, s)
        subject = (
            "ip:" + hmac.new(s.api_key_pepper.get_secret_value().encode(), ip.encode(), hashlib.sha256).hexdigest()[:16]
        )  # 원 IP 는 저장하지 않음
        return Principal("anonymous", subject, subject, plans["anonymous"], ANON_SCOPES)
    parsed = keys.parse(raw)
    if parsed is None:
        raise ApiError(401, "INVALID_API_KEY", "Unauthorized", "API 키 형식이 올바르지 않습니다.")
    key_id, secret = parsed
    row = await _lookup_key(res, key_id, s)
    # 존재하지 않는 키도 같은 비용의 HMAC 비교를 해 응답 시간으로 존재 여부가 드러나지 않게 한다
    stored = row["secret_hmac"] if row else "0" * 64
    ok = keys.verify(s.api_key_pepper.get_secret_value(), secret, stored)
    if not row or not ok:
        raise ApiError(401, "INVALID_API_KEY", "Unauthorized")
    if row["revoked_at"] or row["status"] != "active":
        raise ApiError(401, "KEY_REVOKED", "Unauthorized", "폐기되었거나 정지된 키입니다.")
    if dt.datetime.fromisoformat(row["expires_at"]) <= dt.datetime.now(tz=dt.UTC):
        raise ApiError(401, "KEY_EXPIRED", "Unauthorized", "만료된 키입니다.")
    request.state.touch_key = key_id
    return Principal("key", key_id, row["client_id"], plans[row["plan_id"]], frozenset(row["scopes"]))


async def enforce_rate_limit(request: Request, p: Principal) -> None:
    res: Resources = request.app.state.res
    now = time.time()
    minute = int(now // 60)
    frac = (now % 60) / 60
    script = request.app.state.sliding
    allowed, count, rows = await script(
        keys=[f"al:rl:{p.subject}:{minute}", f"al:rl:{p.subject}:{minute - 1}", _rows_key(p)],
        args=[p.plan.rpm, frac, 120],
    )
    p.rows_used_today = int(rows)
    reset = (minute + 1) * 60
    p.limit_headers = {
        "X-RateLimit-Limit": str(p.plan.rpm),
        "X-RateLimit-Remaining": str(max(p.plan.rpm - int(count), 0)),
        "X-RateLimit-Reset": str(reset),
    }
    if not int(allowed):
        raise ApiError(
            429,
            "RATE_LIMITED",
            "Too Many Requests",
            f"{p.plan.plan_id} 플랜 분당 {p.plan.rpm}회 초과",
            headers={**p.limit_headers, "Retry-After": str(max(int(reset - now), 1))},
        )
    del res


def _rows_key(p: Principal) -> str:
    day = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=9))).date().isoformat()
    return f"al:rows:{p.subject}:{day}"


def remaining_rows(p: Principal) -> int:
    """관문 스크립트가 함께 읽어 온 오늘 사용량으로 판단 (추가 왕복 없음)."""
    left = p.plan.daily_rows - p.rows_used_today
    if left <= 0:
        raise ApiError(
            429,
            "DAILY_ROWS_EXCEEDED",
            "Too Many Requests",
            f"{p.plan.plan_id} 플랜 일일 {p.plan.daily_rows:,}행 한도 소진",
            headers=p.limit_headers,
        )
    return left


async def charge_rows(request: Request, p: Principal, n: int) -> None:
    if n <= 0:
        return
    r = request.app.state.res.redis
    k = _rows_key(p)
    pipe = r.pipeline()
    pipe.incrby(k, n)
    pipe.expire(k, 2 * 86400)
    await pipe.execute()
    request.state.rows = n
