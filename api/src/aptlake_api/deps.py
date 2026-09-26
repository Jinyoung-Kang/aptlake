"""라우트 공통: 스코프 의존성(기본 거부 검증 포함), 데이터셋 버전·ETag, 결과 캐시, 응답 헤더."""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

from .auth import Principal, enforce_rate_limit, resolve_principal
from .errors import ApiError
from .settings import settings

SCOPE_ATTR = "__aptlake_scope__"
KST = dt.timezone(dt.timedelta(hours=9))
PUBLIC_UNSCOPED = {"/healthz", "/readyz", "/docs", "/openapi.json", "/docs/oauth2-redirect", "/redoc"}
DISCLAIMER = "공개 신고 자료를 가공한 학습·포트폴리오용 데이터입니다. 공식 통계가 아니며 투자 판단 근거로 쓰지 마세요."


def require_scope(scope: str) -> Any:
    async def dep(request: Request) -> Principal:
        p = await resolve_principal(request)
        request.state.principal = p
        if scope not in p.scopes:
            raise ApiError(403, "SCOPE_REQUIRED", "Forbidden", f"'{scope}' 스코프가 필요합니다.")
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


class DatasetVersion:
    """발행 파이프라인이 Redis 에 쓴 현재 데이터셋 버전 (프로세스 내 1초 캐시)."""

    def __init__(self) -> None:
        self._at = 0.0
        self._val: tuple[str, str] = ("none", "")

    async def get(self, request: Request) -> tuple[str, str]:
        if time.monotonic() - self._at > 1.0:
            ver, asof = await request.app.state.res.redis.mget("al:ds:ver", "al:ds:asof")
            self._val = (ver or "none", asof or "")
            self._at = time.monotonic()
        return self._val


async def respond(
    request: Request,
    route: str,
    params: dict[str, Any],
    compute: Callable[[], Awaitable[tuple[dict[str, Any], int]]],
    cache: bool = True,
) -> Response:
    """결과 캐시 + ETag/304 + 데이터 기준 헤더 + 행 한도 차감.

    cache=True (데이터 응답): 내용은 (데이터셋 버전, KST 날짜, 요청) 으로 결정된다 → 이 셋으로 캐시 키·ETag 를 만든다.
      날짜를 넣는 이유: '잠정(provisional)' 표시가 발행 없이도 날짜가 지나면 바뀐다.
    cache=False (운영·품질 상태): 발행과 무관하게 바뀌므로 본문을 먼저 만들고 **본문 해시**로 ETag 를 만든다.
      (요청 매개변수만으로 ETag 를 만들면 내용이 바뀌어도 304 를 돌려주는 버그가 된다)
    """
    from .auth import charge_rows, remaining_rows

    p: Principal = request.state.principal
    remaining_rows(p)
    ver, asof = await request.app.state.dsv.get(request)
    today = dt.datetime.now(tz=KST).date().isoformat()
    digest = hashlib.sha256(orjson.dumps([route, params, today], option=orjson.OPT_SORT_KEYS)).hexdigest()[:32]
    headers = {**p.limit_headers, "X-Dataset-Version": ver, "X-Data-As-Of": asof}
    if not cache:
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
        payload, rows = await compute()
        body = orjson.dumps({"dataAsOf": asof or None, "datasetVersion": ver, **payload})
        await r.set(ck, f"{rows}\n".encode() + body, ex=settings().result_cache_ttl_s)
        request.state.cache = "miss"
    else:
        head, _, text = cached.partition("\n")
        rows, body = int(head), text.encode()
        request.state.cache = "hit"
    await charge_rows(request, p, rows)
    headers["X-Cache"] = request.state.cache
    return Response(body, media_type="application/json", headers=headers)
