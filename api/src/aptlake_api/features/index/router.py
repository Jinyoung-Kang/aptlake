"""지수 API (HTTP 계층)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.clock import provisional
from ...core.http import require_scope, respond
from . import service
from .repository import IndexRepository

router = APIRouter(prefix="/v1")


@router.get("/index", summary="자체 지수 시계열 + R-ONE 대비 검증 지표")
async def price_index(
    request: Request,
    regionId: Annotated[str, Query(pattern=r"^[0-9]{2}$", description="시도 2자리, 전국 00")],  # noqa: N803
    method: Annotated[str, Query(pattern=r"^HEDONIC_TD_v1$")] = service.METHOD,
    p: Principal = require_scope("read"),
) -> Response:
    return await respond(
        request,
        "index",
        {"r": regionId, "m": method},
        lambda: service.region_index(
            IndexRepository(request.app.state.res.ch), regionId, method, is_provisional=provisional
        ),
    )


@router.get("/index/summary", summary="지역별 자체 지수 최근 값·변화율·R-ONE 검증 (표용)")
async def index_summary(request: Request, p: Principal = require_scope("read")) -> Response:
    return await respond(
        request,
        "index_summary",
        {},
        lambda: service.summary(IndexRepository(request.app.state.res.ch), is_provisional=provisional),
    )
