"""품질·계보 API (HTTP 계층). 운영 상태라 발행과 무관하게 바뀌므로 결과 캐시 없이 본문 해시 ETag (cache=False)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.http import require_scope, respond
from ...core.params import SGG, YM_Q
from ...core.values import ym_to_date
from . import service
from .repository import QualityRepository

router = APIRouter(prefix="/v1")


def _repo(request: Request) -> QualityRepository:
    return QualityRepository(request.app.state.res.pg)


@router.get("/quality/summary", summary="전체 신선도·파티션 상태·최근 실패 검사")
async def quality_summary(request: Request, p: Principal = require_scope("read")) -> Response:
    return await respond(request, "quality_summary", {}, lambda: service.summary(_repo(request)), cache=False)


@router.get("/quality/partitions", summary="파티션 상태 격자 (시군구 × 계약월) — 품질 히트맵용")
async def quality_grid(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    sido: Annotated[
        str | None, Query(pattern=r"^[0-9]{2}$", description="시도 2자리 — 지정하면 그 시도 시군구만")
    ] = None,
    p: Principal = require_scope("read"),
) -> Response:
    start, end = ym_to_date(from_), ym_to_date(to)
    service.check_span(start, end, service.SIDO_GRID_MAX_MONTHS if sido else service.GRID_MAX_MONTHS)
    return await respond(
        request,
        "quality_grid",
        {"a": from_, "b": to, "s": sido},
        lambda: service.grid(_repo(request), from_, to, start, end, sido),
        cache=False,
    )


@router.get("/quality/rollup", summary="시도 × 계약월 수집 완결도 (파티션 상태별 개수)")
async def quality_rollup(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    start, end = ym_to_date(from_), ym_to_date(to)
    service.check_span(start, end, service.SIDO_GRID_MAX_MONTHS)
    return await respond(
        request,
        "quality_rollup",
        {"a": from_, "b": to},
        lambda: service.rollup(_repo(request), from_, to, start, end),
        cache=False,
    )


@router.get("/quality/partitions/{sggCd}/{dealYm}", summary="파티션 품질 검사 결과·계보")
async def quality_partition(
    request: Request,
    sggCd: SGG,  # noqa: N803
    dealYm: Annotated[str, Path(pattern=YM_Q)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    return await respond(
        request,
        "quality_partition",
        {"s": sggCd, "m": dealYm.replace("-", "")},
        lambda: service.partition(_repo(request), sggCd, dealYm),
        cache=False,
    )
