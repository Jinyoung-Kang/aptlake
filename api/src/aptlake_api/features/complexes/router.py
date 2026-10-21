"""단지 API (HTTP 계층)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.http import require_scope, respond
from ..rent.repository import RentRepository
from . import service
from .repository import ComplexRepository

router = APIRouter(prefix="/v1")
COMPLEX_KEY = r"^c_[0-9a-f]{20}$"


@router.get(
    "/complexes/{complexKey}",
    summary="단지 정보 + 최근 거래",
    description="complex.landLeasehold: 토지임대부 여부 — 매매 원천에만 있는 항목이라 전월세에만 나온 단지는 null(미상). "
    "rent: 최근 12개월(전월세 자료의 마지막 계약월까지) 전용면적 구간별 전세·월세와 단지 전세가율(자체 산출). "
    "rent.window.complete=false 면 창의 일부 달 자료가 아직 발행되지 않아 구간별 ratioPct 는 null.",
)
async def complex_detail(
    request: Request,
    complexKey: Annotated[str, Path(pattern=COMPLEX_KEY)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    res = request.app.state.res
    return await respond(
        request,
        "complex",
        {"k": complexKey},
        lambda: service.detail(ComplexRepository(res.ch), complexKey, RentRepository(res.ch, res.pg)),
        source="all",  # 매매 + 전월세 블록 — 기준 시각은 두 원천 중 이른 쪽
    )


@router.get("/search", summary="단지명·법정동 검색 (시군구 검색은 화면에서 처리)")
async def search(
    request: Request, q: Annotated[str, Query(min_length=1, max_length=40)], p: Principal = require_scope("read")
) -> Response:
    term = service.search_term(q)
    return await respond(
        request, "search", {"q": term}, lambda: service.search(ComplexRepository(request.app.state.res.ch), term)
    )
