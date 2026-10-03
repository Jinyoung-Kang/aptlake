"""단지 API (HTTP 계층)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.http import require_scope, respond
from . import service
from .repository import ComplexRepository

router = APIRouter(prefix="/v1")
COMPLEX_KEY = r"^c_[0-9a-f]{20}$"


@router.get("/complexes/{complexKey}", summary="단지 정보 + 최근 거래")
async def complex_detail(
    request: Request,
    complexKey: Annotated[str, Path(pattern=COMPLEX_KEY)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    return await respond(
        request,
        "complex",
        {"k": complexKey},
        lambda: service.detail(ComplexRepository(request.app.state.res.ch), complexKey),
    )


@router.get("/search", summary="단지명·법정동 검색 (시군구 검색은 화면에서 처리)")
async def search(
    request: Request, q: Annotated[str, Query(min_length=1, max_length=40)], p: Principal = require_scope("read")
) -> Response:
    term = service.search_term(q)
    return await respond(
        request, "search", {"q": term}, lambda: service.search(ComplexRepository(request.app.state.res.ch), term)
    )
