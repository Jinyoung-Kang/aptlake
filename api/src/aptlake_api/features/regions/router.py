"""지역 API (HTTP 계층): 매개변수 검증·스코프·캐시만. 규칙은 service, 데이터는 repository."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.clock import provisional
from ...core.http import require_scope, respond
from ...core.params import SGG, YM_Q
from ...core.plans import check_range
from ...core.settings import settings
from ...core.values import month_end, ym_to_date
from . import service
from .repository import RegionRepository

router = APIRouter(prefix="/v1")


def _repo(request: Request) -> RegionRepository:
    return RegionRepository(request.app.state.res.ch)


@router.get("/regions", summary="시군구 목록·코드")
async def regions(request: Request, p: Principal = require_scope("read")) -> Response:
    return await respond(request, "regions", {}, lambda: service.regions(_repo(request)))


@router.get("/regions/{sggCd}/months", summary="월별 거래량·해제·㎡당 가격 분위수")
async def region_months(
    request: Request,
    sggCd: SGG,  # noqa: N803
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    start, end = ym_to_date(from_), ym_to_date(to)
    check_range(p.plan.plan_id, p.plan.max_range_months, start, end)

    async def compute():
        repo = _repo(request)
        ver, _ = await request.app.state.dsv.get(request)
        names = await repo.by_code(ver)
        return await service.region_months(
            repo,
            names,
            sggCd,
            start,
            end,
            is_provisional=provisional,
            provisional_days=settings().provisional_days,
        )

    return await respond(request, "region_months", {"sgg": sggCd, "a": from_, "b": to}, compute)


@router.get("/regions/{sggCd}/distribution", summary="한 달 가격 분포: ㎡당 가격 히스토그램·면적대·층별·산점도")
async def distribution(
    request: Request,
    sggCd: SGG,  # noqa: N803
    ym: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    month = ym_to_date(ym)
    return await respond(
        request,
        "distribution",
        {"s": sggCd, "m": ym},
        lambda: service.distribution(
            _repo(request), sggCd, ym, month, month_end(month), provisional=provisional(month)
        ),
    )


@router.get("/regions/{sggCd}/complexes", summary="기간 내 거래가 많은 단지 (단지별 중위가·마지막 거래)")
async def region_complexes(
    request: Request,
    sggCd: SGG,  # noqa: N803
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    p: Principal = require_scope("read"),
) -> Response:
    a, b = ym_to_date(from_), month_end(ym_to_date(to))
    service.check_period(a, b)
    return await respond(
        request,
        "region_complexes",
        {"s": sggCd, "a": from_, "b": to, "l": limit},
        lambda: service.top_complexes(_repo(request), sggCd, from_, to, a, b, limit),
    )
