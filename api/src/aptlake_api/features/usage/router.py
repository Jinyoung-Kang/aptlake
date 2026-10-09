"""내 사용량 API (HTTP 계층). 키마다 다른 응답이라 결과 캐시를 쓰지 않는다."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ...core.auth import Principal
from ...core.http import require_scope
from ...core.responses import OrjsonResponse
from . import service
from .repository import UsageRepository

router = APIRouter(prefix="/v1")


@router.get("/me/usage", summary="내 키(클라이언트) 일별 사용량")
async def my_usage(
    request: Request, days: Annotated[int, Query(ge=1, le=90)] = 30, p: Principal = require_scope("read")
) -> OrjsonResponse:
    service.require_key(p.kind)
    body = await service.my_usage(
        UsageRepository(request.app.state.res.ch),
        client_id=p.client_id,
        plan=p.plan,
        rows_used_today=p.rows_used_today,
        days=days,
    )
    return OrjsonResponse(body, headers=p.limit_headers)


@router.get("/me/usage/monthly", summary="내 고객(모든 키 합) 달별 과금 대상 단위 — 요청·차감 행·내보내기")
async def my_usage_monthly(
    request: Request, months: Annotated[int, Query(ge=1, le=6)] = 6, p: Principal = require_scope("read")
) -> OrjsonResponse:
    service.require_key(p.kind)
    repo = UsageRepository(request.app.state.res.ch, request.app.state.res.pg)
    return OrjsonResponse(await service.monthly(repo, p.client_id, months), headers=p.limit_headers)


@router.get("/plans", summary="공개 플랜과 한도 (키 없이도)")
async def plans(request: Request, p: Principal = require_scope("read")) -> OrjsonResponse:
    body = {"plans": service.public_plans(list(request.app.state.plans.values()))}
    return OrjsonResponse(body, headers={**p.limit_headers, "Cache-Control": "public, max-age=300"})
