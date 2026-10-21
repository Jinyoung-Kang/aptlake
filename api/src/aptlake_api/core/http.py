"""HTTP 계층 공통: 스코프 의존성(기본 거부 검증 포함), 데이터셋 버전·ETag, 결과 캐시, 응답 헤더."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast

import orjson
import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

from .auth import Principal, enforce_rate_limit, resolve_principal
from .clock import KST
from .problems import ApiError
from .settings import settings
from .singleflight import SingleFlight
from .texts import DISCLAIMER  # noqa: F401 — 아직 옮기지 않은 라우트가 여기서 가져간다

SCOPE_ATTR = "__aptlake_scope__"


PUBLIC_UNSCOPED = {"/healthz", "/readyz", "/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc"}


def code_revision(root: Path) -> str:
    """패키지 소스(.py)의 지문. 응답 모양·계산 규칙은 코드에 있으므로 결과 캐시 키·ETag 에 넣는다 —
    빼면 응답이 바뀌는 배포 뒤에도 같은 버전·같은 날에는 옛 본문이 캐시와 304 로 계속 나간다."""
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py")):
        h.update(f.relative_to(root).as_posix().encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


CODE_REV = code_revision(Path(__file__).resolve().parents[1])


def require_scope(scope: str, *alternatives: str) -> Any:
    """scope 또는 alternatives 중 하나가 있어야 한다 (예: 수집 상태 보기 = ops 또는 ops_read)."""
    accepted = (scope, *alternatives)

    async def dep(request: Request) -> Principal:
        p = await resolve_principal(request)
        request.state.principal = p
        if not any(s in p.scopes for s in accepted):
            names = " 또는 ".join(f"'{s}'" for s in accepted)
            raise ApiError(403, "SCOPE_REQUIRED", "Forbidden", f"{names} 스코프가 필요합니다.")
        await enforce_rate_limit(request, p)
        return p

    setattr(dep, SCOPE_ATTR, scope)
    return Depends(dep)


def iter_api_routes(routes: list) -> Any:
    """앱의 모든 APIRoute. FastAPI 0.14x 는 include_router 결과를 _IncludedRouter 로 감싸므로 안쪽까지 내려간다."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from iter_api_routes(inner.routes)


def assert_all_routes_scoped(app: FastAPI) -> None:
    """모든 라우트가 require_scope 를 선언했는지 기동 시 검사 (빠뜨리면 기동 실패 = 기본 거부)."""
    missing = [
        r.path
        for r in iter_api_routes(app.routes)
        if r.path not in PUBLIC_UNSCOPED and not any(hasattr(d.call, SCOPE_ATTR) for d in r.dependant.dependencies)
    ]
    if missing:
        raise RuntimeError(f"routes without scope dependency: {missing}")


# 데이터 기준 시각의 원천 (ADR-050): 발행 파이프라인이 원천별 키(al:ds:asof:trade·al:ds:asof:rent)에 쓴다.
# 'all' = 두 원천을 함께 쓰는 응답(전세가율·시장 지도·단지 상세·수집 상태)
AsOfSource = Literal["trade", "rent", "all"]
ASOF_SOURCES: tuple[str, ...] = ("trade", "rent")
ASOF_KEYS = tuple(f"al:ds:asof:{s}" for s in ASOF_SOURCES)
# 원천별 키 이전 파이프라인이 쓰던 전역 키. 값은 쓰지 않는다(아래 data_as_of) — 있으면 그 파이프라인이 발행한 상태라는 표시만
LEGACY_ASOF_KEY = "al:ds:asof"
# 발행 기록의 원천별 기준 시각 — 파이프라인이 원천별 키에 쓰는 값과 같은 규칙 (ops_db.data_as_of_by_source)
RECORDED_AS_OF_SQL = "SELECT source, max(data_as_of) AS t FROM ops.dataset_version GROUP BY source"
# 발행 기록을 읽을 때 풀의 연결을 기다리는 상한(초) — 풀 기본값(30초)이면 운영 DB 장애 동안 운영 DB 를 쓰지 않는 조회까지
# 그만큼 멈춘다. 받은 연결의 질의는 서버에 닿으면 statement_timeout 이 막지만, 닿지 않으면 막지 못한다 (아래 상한)
RECORDED_AS_OF_WAIT_S = 1.0
# 요청이 발행 기록 조회를 기다리는 상한(초) = 연결 대기 + 질의 몫 1초. 받은 연결이 서버에 닿지 않으면(네트워크 멈춤, 컨테이너를
# 다시 만들어 유휴 연결이 RST 없이 끊김) 질의가 TCP 시간 초과까지 끝나지 않는다 — 상한이 없으면 그동안 모든 응답(매매만 쓰는
# 응답도)이 그 조회 하나를 함께 기다린다
RECORDED_AS_OF_DEADLINE_S = RECORDED_AS_OF_WAIT_S + 1.0
# 발행 기록을 못 읽은 뒤 다시 읽지 않는 시간(초) — 장애 동안 1초 갱신마다 기다리지 않게
RECORDED_AS_OF_RETRY_S = 10.0


def data_as_of(asof: dict[str, str], source: str) -> str:
    """응답에 싣는 데이터 기준 시각 (모르면 빈 값).

    'all' 은 두 원천 중 이른 쪽 — 한쪽이라도 없거나 읽을 수 없으면 모른다(빈 값).
    옛 전역 키(al:ds:asof)로 돌아가지 않는다: 마지막에 발행한 원천의 시각이라 다른 원천의 응답에는 틀린 값이다.
    """
    if source != "all":
        return asof.get(source, "")
    vals = [asof.get(s, "") for s in ASOF_SOURCES]
    if not all(vals):
        return ""
    try:  # 글자 비교가 아니라 시각 비교 (시간대 표기가 달라도)
        return min(vals, key=dt.datetime.fromisoformat)
    except (ValueError, TypeError):  # 형식 오류 · 시간대 없는 값과 있는 값의 비교
        return ""


class DatasetVersion:
    """발행 파이프라인이 Redis 에 쓴 현재 데이터셋 버전과 원천별 데이터 기준 시각 (한 번의 MGET, 프로세스 내 1초 캐시).

    버전 번호는 원천과 관계없이 하나로 오르지만, 기준 시각(그 원천의 마지막 수집 시각)은 원천마다 다르다.
    전환 구간: 원천별 키를 쓰기 전 파이프라인(옛 전역 키만 쓴다)의 발행만 있었으면 원천별 키가 없다. 이때(옛 키가 있고 원천별 키가
    빠졌을 때)만 빠진 원천의 값을 발행 기록에서 읽는다 — API 를 파이프라인보다 먼저 배포해도 dataAsOf 가 비지 않게.
    원천별 키를 쓰는 파이프라인은 발행 때 옛 키를 지우므로 그 뒤로는 읽지 않는다.
    발행 기록은 버전을 올리기 전에 커밋되므로 버전마다 한 번만 읽는다. 못 읽으면(운영 DB 장애) 그 순간의 값은 불완전하다
    (degraded) — 그 때문에 기준 시각이 빈 응답은 respond 가 결과 캐시·버전 ETag 에 남기지 않는다. 동시에 갱신하는 요청은
    한 조회를 함께 기다리되 상한까지만 기다리고, 실패하거나 상한을 넘긴 뒤에는 잠시 다시 읽지 않는다.
    """

    def __init__(self) -> None:
        self._at = 0.0
        self._val: tuple[str, dict[str, str], bool] = ("none", {}, False)
        self._recorded: tuple[str, dict[str, str]] | None = None  # (버전, 발행 기록의 원천별 기준 시각)
        self._retry_at = 0.0  # 발행 기록을 못 읽었으면 이 시각(monotonic)까지 다시 읽지 않는다
        self._flight = SingleFlight()

    async def get(self, request: Request) -> tuple[str, dict[str, str], bool]:
        """(버전, {원천: 기준 시각}, degraded) — 모르면 빈 값. degraded = 빠진 원천을 발행 기록에서 읽어야 했는데 못 읽었다."""
        if time.monotonic() - self._at > 1.0:
            res = request.app.state.res
            ver, legacy, *vals = await res.redis.mget("al:ds:ver", LEGACY_ASOF_KEY, *ASOF_KEYS)
            ver = ver or "none"
            asof = {s: v or "" for s, v in zip(ASOF_SOURCES, vals, strict=True)}
            degraded = False
            if legacy and not all(asof.values()):
                recorded = await self._recorded_as_of(res.pg, ver)
                degraded = recorded is None
                asof = {s: v or (recorded or {}).get(s, "") for s, v in asof.items()}
            self._val = (ver, asof, degraded)
            self._at = time.monotonic()
        return self._val

    async def _recorded_as_of(self, pg: Any, ver: str) -> dict[str, str] | None:
        """발행 기록의 원천별 기준 시각 (버전마다 한 번 읽어 기억한다). 못 읽으면 None."""
        if self._recorded is not None and self._recorded[0] == ver:
            return self._recorded[1]
        if time.monotonic() < self._retry_at:
            return None
        try:  # 조회는 SingleFlight 가 shield 로 기다리므로 상한이 지나도 취소되지 않는다 — 늦게라도 성공하면 그 값을 기억한다
            async with asyncio.timeout(RECORDED_AS_OF_DEADLINE_S):
                recorded, _ = await self._flight.do(ver, lambda: self._read_recorded(pg, ver))
        except TimeoutError:
            # 실패처럼 잠시 다시 기다리지 않는다. 그 뒤에도 조회가 남아 있으면 새 연결 없이 그 조회를 다시 기다린다
            self._retry_at = time.monotonic() + RECORDED_AS_OF_RETRY_S
            return None
        return recorded

    async def _read_recorded(self, pg: Any, ver: str) -> dict[str, str] | None:
        try:
            async with pg.connection(timeout=RECORDED_AS_OF_WAIT_S) as c:
                rows = cast(list[dict[str, Any]], await (await c.execute(RECORDED_AS_OF_SQL)).fetchall())
        except psycopg.Error:  # 연결 대기 초과(PoolTimeout)도 여기 — 실패는 기억하지 않고 잠시 뒤 다시 읽는다
            self._retry_at = time.monotonic() + RECORDED_AS_OF_RETRY_S
            return None
        # 파이프라인이 키에 쓰는 모양(UTC ISO 8601)과 같게
        self._recorded = (ver, {r["source"]: r["t"].astimezone(dt.UTC).isoformat() for r in rows if r["t"]})
        return self._recorded[1]


async def respond(
    request: Request,
    route: str,
    params: dict[str, Any],
    compute: Callable[[], Awaitable[tuple[dict[str, Any], int]]],
    cache: bool = True,
    source: AsOfSource = "trade",
) -> Response:
    """결과 캐시 + ETag/304 + 데이터 기준 헤더 + 행 한도 차감.

    cache=True (데이터 응답): 내용은 (데이터셋 버전, KST 날짜, 요청, 코드판) 으로 결정된다 → 이 넷으로 캐시 키·ETag 를 만든다.
      날짜를 넣는 이유: '잠정(provisional)' 표시가 발행 없이도 날짜가 지나면 바뀐다.
      그래서 cache=True 응답이 운영 DB 를 읽는다면 발행(버전)과 함께만 바뀌는 기록만 써야 한다 — 수집 상태처럼 발행 없이
      바뀌는 값을 쓰면 같은 버전에서 캐시된 본문·304 와 새로 계산한 본문이 어긋난다.
    cache=False (운영·품질 상태): 발행과 무관하게 바뀌므로 본문을 먼저 만들고 **본문 해시**로 ETag 를 만든다.
      (요청 매개변수만으로 ETag 를 만들면 내용이 바뀌어도 304 를 돌려주는 버그가 된다)
      데이터 응답도 그 원천의 기준 시각을 운영 DB 장애로 못 읽은 순간(degraded — DatasetVersion 의 전환 대체 경로)에는
      이렇게 보낸다: 그 순간의 본문(dataAsOf null)이 같은 버전의 캐시 키·ETag 로 남으면 복구 뒤에도 캐시·304 로 계속 나간다.
    source: 응답 데이터의 원천 — dataAsOf·X-Data-As-Of 를 그 원천의 기준 시각으로 ('all' = 두 원천 중 이른 쪽).
      기준 시각이 바뀌는 발행은 버전도 올리므로 캐시 키는 그대로 (버전, 날짜, 요청)이다.
    """
    from .auth import charge_rows, remaining_rows

    p: Principal = request.state.principal
    remaining_rows(p)
    ver, asofs, degraded = await request.app.state.dsv.get(request)
    asof = data_as_of(asofs, source)
    today = dt.datetime.now(tz=KST).date().isoformat()
    request_key = orjson.dumps([route, params, today, CODE_REV], option=orjson.OPT_SORT_KEYS)
    digest = hashlib.sha256(request_key).hexdigest()[:32]
    headers = {**p.limit_headers, "X-Dataset-Version": ver, "X-Data-As-Of": asof}
    unsettled = degraded and not asof  # 복구 뒤 같은 버전·요청의 본문과 달라진다 (원천별 키가 있는 원천은 그대로)
    if not cache or unsettled:
        payload, rows = await compute()
        body = orjson.dumps({"dataAsOf": asof or None, "datasetVersion": ver, **payload})
        etag = f'"{hashlib.sha256(body).hexdigest()[:24]}"'
        headers.update({"ETag": etag, "Cache-Control": "private, no-cache", "X-Cache": "bypass"})
        request.state.cache = "bypass"
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        await charge_rows(request, p, rows)
        return Response(body, media_type="application/json", headers=headers)

    etag = f'"{hashlib.sha256(f"{ver}:{digest}".encode()).hexdigest()[:24]}"'
    headers.update({"ETag": etag, "Cache-Control": "private, max-age=60"})
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    r = request.app.state.res.redis
    ck = f"al:cache:{ver}:{route}:{digest}"
    # 캐시 값 = "<행 수>\n<본문>" 한 키 (왕복 1회)
    cached: str | None = await r.get(ck)
    if cached is None:

        async def produce() -> tuple[int, bytes]:
            payload, rows = await compute()
            body = orjson.dumps({"dataAsOf": asof or None, "datasetVersion": ver, **payload})
            await r.set(ck, f"{rows}\n".encode() + body, ex=settings().result_cache_ttl_s)
            return rows, body

        # 같은 키를 이미 계산 중이면 그 결과를 기다린다 (캐시가 빈 순간 몰린 같은 요청이 각자 질의하지 않게, QA-009)
        (rows, body), leader = await request.app.state.flight.do(ck, produce)
        request.state.cache = "miss" if leader else "shared"
    else:
        head, _, text = cached.partition("\n")
        rows, body = int(head), text.encode()
        request.state.cache = "hit"
    await charge_rows(request, p, rows)
    headers["X-Cache"] = request.state.cache
    return Response(body, media_type="application/json", headers=headers)
