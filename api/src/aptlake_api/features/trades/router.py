"""거래 API (HTTP 계층)."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from ...core import cursor
from ...core.auth import Principal
from ...core.http import require_scope, respond
from ...core.plans import check_range
from ...core.settings import settings
from . import service
from .repository import TradeRepository

router = APIRouter(prefix="/v1")


@router.get("/trades", summary="거래 목록 (커서 페이지, 최신 계약일 순)")
async def trades(
    request: Request,
    sggCd: Annotated[str, Query(pattern=r"^[0-9]{5}$")],  # noqa: N803
    from_: Annotated[dt.date, Query(alias="from")],
    to: dt.date,
    minArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    maxArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    includeCancelled: bool = False,  # noqa: N803
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor_token: Annotated[str | None, Query(alias="cursor", max_length=400)] = None,
    p: Principal = require_scope("read"),
) -> Response:
    check_range(p.plan.plan_id, p.plan.max_range_months, from_, to)
    service.check_page_size(p.plan.plan_id, p.plan.max_page_size, limit)
    f = service.TradeFilter(sggCd, from_, to, minArea, maxArea, includeCancelled)
    query = f.key(limit)
    fp = cursor.query_fingerprint(query)
    key = settings().cursor_signing_key.get_secret_value().encode()
    pos = service.read_cursor(key, cursor_token, fp)
    return await respond(
        request,
        "trades",
        {**query, "cursor": cursor_token},
        lambda: service.page(TradeRepository(request.app.state.res.ch), f, limit, pos, cursor_key=key, fingerprint=fp),
    )


@router.get("/trades/{tradeId}/history", summary="거래 버전 이력 (SCD2)")
async def trade_history(
    request: Request,
    tradeId: Annotated[str, Path(max_length=40)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    yyyymm = service.parse_trade_id(tradeId)
    return await respond(
        request,
        "trade_history",
        {"id": tradeId},
        lambda: service.history(TradeRepository(request.app.state.res.ch), tradeId, yyyymm),
    )
