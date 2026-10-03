"""시장 개요·시세 띠·지도 경계 API (HTTP 계층)."""

from __future__ import annotations

import asyncio
import gzip
from typing import Annotated

import orjson
from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.clock import provisional
from ...core.http import require_scope, respond
from ...core.params import YM_Q
from ...core.problems import ApiError
from ...core.values import ym_to_date
from . import service
from .repository import BoundaryRepository, MarketRepository

router = APIRouter(prefix="/v1")


@router.get("/market/overview", summary="전국·시도·시군구 한 달 요약 + 순위 (지도·표용)")
async def overview(
    request: Request, ym: Annotated[str | None, Query(pattern=YM_Q)] = None, p: Principal = require_scope("read")
) -> Response:
    return await respond(
        request,
        "market_overview",
        {"ym": ym},
        lambda: service.overview(
            MarketRepository(request.app.state.res.ch), ym_to_date(ym) if ym else None, is_provisional=provisional
        ),
    )


@router.get("/market/ticker", summary="상단 지표 띠: 전국 거래·중위가, 자체 지수 월간 변화")
async def ticker(request: Request, p: Principal = require_scope("read")) -> Response:
    return await respond(
        request,
        "market_ticker",
        {},
        lambda: service.ticker(MarketRepository(request.app.state.res.ch), is_provisional=provisional),
    )


def _encode(meta: dict, shapes: list[dict]) -> tuple[bytes, bytes]:
    body = orjson.dumps(service.feature_collection(meta, shapes))
    return body, gzip.compress(body, compresslevel=9, mtime=0)


@router.get("/geo/sgg", summary="시군구 경계 GeoJSON (시각화용 단순화본, V-World)")
async def geo_sgg(request: Request, p: Principal = require_scope("read")) -> Response:
    cache = request.app.state.geo_cache
    repo = BoundaryRepository(request.app.state.res.pg)
    meta = await repo.meta()
    if not meta or not meta["n"]:
        raise ApiError(404, "BOUNDARY_NOT_READY", "Not Found", "경계 데이터가 아직 없습니다 (make lake-init).")
    etag = service.boundary_etag(meta)
    headers = {**p.limit_headers, "ETag": etag, "Cache-Control": "public, max-age=86400"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    if cache.get("etag") != etag:
        async with request.app.state.geo_lock:  # 같은 워커의 동시 첫 요청은 한 번만 만든다
            if cache.get("etag") != etag:
                shapes = await repo.shapes()
                # 직렬화·압축(1.1MB, 수백 ms)은 스레드에서 — 그동안 이 워커의 다른 요청이 멈추지 않게.
                # 데이터 버전당 한 번만 만들어 둔다
                body, gz = await asyncio.to_thread(_encode, meta, shapes)
                cache.update(etag=etag, body=body, gz=gz)
    headers["Vary"] = "Accept-Encoding"
    if "gzip" in request.headers.get("accept-encoding", ""):
        # Content-Encoding 이 이미 있으면 GZip 미들웨어는 다시 압축하지 않고 그대로 보낸다
        return Response(cache["gz"], media_type="application/geo+json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(cache["body"], media_type="application/geo+json", headers=headers)
