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
        plan_id=p.plan.plan_id,
        rpm=p.plan.rpm,
        daily_rows=p.plan.daily_rows,
        days=days,
    )
    return OrjsonResponse(body, headers=p.limit_headers)
