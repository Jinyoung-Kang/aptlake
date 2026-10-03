"""수집 상태 · 오류 로그 · 연결 점검 API (/v1/ops, HTTP 계층).

'ops' 스코프가 있는 키(웹 BFF 키·운영자 키)만 — 익명·일반 데이터 키에는 내부 운영 정보를 주지 않는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from ...core.auth import Principal
from ...core.clock import kst_today
from ...core.http import require_scope, respond
from ...core.responses import OrjsonResponse
from ...core.settings import settings
from . import service
from .repository import OpsRepository

router = APIRouter(prefix="/v1/ops")
SOURCE = Literal["all", "pipeline", "ingest", "quality", "api"]


def _repo(request: Request) -> OpsRepository:
    res = request.app.state.res
    return OpsRepository(res.pg, res.ch, res.redis)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


@router.get("/status", summary="수집 상태: 요약 · 작업 큐 · 스케줄/센서")
async def status(request: Request, p: Principal = require_scope("ops", "ops_read")) -> Response:
    async def compute():
        return await service.status(
            request.app.state.dagster,
            _repo(request),
            today=kst_today(),
            budget_pct=settings().rtms_budget_pct,
            now=_utcnow(),
        )

    return await respond(request, "ops_status", {}, compute, cache=False)


@router.get("/errors", summary="오류 로그 (파이프라인·원천 수집·품질 검사·API)")
async def errors(
    request: Request,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    source: SOURCE = "all",
    includeResolved: bool = False,  # noqa: N803
    includeCleared: bool = False,  # noqa: N803 — '비우기' 이전 항목도 (흐리게) 보기
    p: Principal = require_scope("ops", "ops_read"),
) -> Response:
    return await respond(
        request,
        "ops_errors",
        {"h": hours, "s": source, "r": includeResolved, "c": includeCleared},
        lambda: service.errors(
            request.app.state.dagster,
            _repo(request),
            hours=hours,
            source=source,
            include_resolved=includeResolved,
            include_cleared=includeCleared,
            now=_utcnow(),
        ),
        cache=False,
    )


@router.post("/errors/clear", summary="오류 로그 비우기 — 지금 이전 항목을 숨김 (원본 기록은 지우지 않음, 감사 로그)")
async def clear_errors(request: Request, p: Principal = require_scope("ops")) -> Response:
    return OrjsonResponse(await service.set_cleared(_repo(request), True, p.subject), headers=p.limit_headers)


@router.delete("/errors/clear", summary="오류 로그 비우기 되돌리기")
async def restore_errors(request: Request, p: Principal = require_scope("ops")) -> Response:
    return OrjsonResponse(await service.set_cleared(_repo(request), False, p.subject), headers=p.limit_headers)


@router.get(
    "/connectivity", summary="API 연결 점검: API 서버가 의존하는 구성요소의 응답 여부·지연, 원천 수집 최근 상태"
)
async def connectivity(request: Request, p: Principal = require_scope("ops", "ops_read")) -> Response:
    """구성요소마다 가장 가벼운 질의 한 번 (동시 실행, 각 2초 제한).

    외부 원천(국토부·V-World)은 **직접 호출하지 않는다** — 일일 호출 한도가 있는 키를 공개 화면의 버튼으로
    소모하게 만들 수 없으므로, 파이프라인이 기록한 마지막 성공 수집 시각·오늘 예산 상태를 보여 준다.
    """
    return await respond(
        request,
        "ops_connectivity",
        {},
        lambda: service.connectivity(
            request.app.state.dagster,
            _repo(request),
            today=kst_today(),
            budget_pct=settings().rtms_budget_pct,
            now=_utcnow,
        ),
        cache=False,
    )
