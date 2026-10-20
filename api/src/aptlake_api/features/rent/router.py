"""전월세 API (HTTP 계층, ADR-050): 매개변수 검증·스코프·캐시만. 규칙은 service, 데이터는 repository."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from ...core import cursor
from ...core.auth import Principal
from ...core.clock import provisional
from ...core.http import require_scope, respond
from ...core.params import SGG, YM_Q
from ...core.plans import check_range
from ...core.settings import settings
from ...core.values import month_end, ym_to_date
from ..regions.repository import RegionRepository
from ..trades.service import check_page_size
from . import service
from .repository import RentRepository

router = APIRouter(prefix="/v1")


def _repo(request: Request) -> RentRepository:
    return RentRepository(request.app.state.res.ch)


async def _names(request: Request) -> dict:
    ver, _ = await request.app.state.dsv.get(request)
    return await RegionRepository(request.app.state.res.ch, ver).by_code(ver)


@router.get("/regions/{sggCd}/rent/months", summary="전월세 월별 계약 수·전세 보증금 ㎡당 분위수·월세 중앙값")
async def rent_months(
    request: Request,
    sggCd: SGG,  # noqa: N803
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    start, end = ym_to_date(from_), ym_to_date(to)
    check_range(p.plan.plan_id, p.plan.max_range_months, start, end)

    async def compute():
        return await service.rent_months(
            _repo(request),
            await _names(request),
            sggCd,
            start,
            end,
            is_provisional=provisional,
            provisional_days=settings().provisional_days,
        )

    return await respond(request, "rent_months", {"sgg": sggCd, "a": from_, "b": to}, compute)


@router.get(
    "/regions/{sggCd}/jeonse-ratio", summary="전세가율 — 3개월 창의 전세 보증금 ㎡당 ÷ 매매 ㎡당 중앙값 (자체 산출)"
)
async def jeonse_ratio(
    request: Request,
    sggCd: SGG,  # noqa: N803
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    start, end = ym_to_date(from_), ym_to_date(to)
    check_range(p.plan.plan_id, p.plan.max_range_months, start, end)

    async def compute():
        return await service.jeonse_ratio(
            _repo(request), await _names(request), sggCd, start, end, is_provisional=provisional
        )

    return await respond(request, "jeonse_ratio", {"sgg": sggCd, "a": from_, "b": to}, compute)


@router.get("/regions/{sggCd}/rent/distribution", summary="한 달 전월세 분포: 전세 보증금 ㎡당 히스토그램·면적대")
async def rent_distribution(
    request: Request,
    sggCd: SGG,  # noqa: N803
    ym: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    month = ym_to_date(ym)
    return await respond(
        request,
        "rent_distribution",
        {"s": sggCd, "m": ym},
        lambda: service.distribution(
            _repo(request), sggCd, ym, month, month_end(month), provisional=provisional(month)
        ),
    )


@router.get("/rents", summary="전월세 계약 목록 (커서 페이지, 최신 계약일 순)")
async def rents(
    request: Request,
    sggCd: Annotated[str, Query(pattern=r"^[0-9]{5}$")],  # noqa: N803
    from_: Annotated[dt.date, Query(alias="from")],
    to: dt.date,
    minArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    maxArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    kind: Annotated[
        Literal["jeonse", "wolse"] | None, Query(description="jeonse = 월세 0 인 계약, wolse = 그 밖")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor_token: Annotated[str | None, Query(alias="cursor", max_length=400)] = None,
    p: Principal = require_scope("read"),
) -> Response:
    check_range(p.plan.plan_id, p.plan.max_range_months, from_, to)
    check_page_size(p.plan.plan_id, p.plan.max_page_size, limit)
    f = service.RentFilter(sggCd, from_, to, minArea, maxArea, kind)
    query = f.key(limit)
    fp = cursor.query_fingerprint(query)
    key = settings().cursor_signing_key.get_secret_value().encode()
    pos = service.read_cursor(key, cursor_token, fp)
    return await respond(
        request,
        "rents",
        {**query, "cursor": cursor_token},
        lambda: service.page(_repo(request), f, limit, pos, cursor_key=key, fingerprint=fp),
    )


@router.get("/rents/{rentId}/history", summary="전월세 계약 버전 이력 (SCD2)")
async def rent_history(
    request: Request,
    rentId: Annotated[str, Path(max_length=40)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    yyyymm = service.parse_rent_id(rentId)
    return await respond(
        request, "rent_history", {"id": rentId}, lambda: service.history(_repo(request), rentId, yyyymm)
    )
