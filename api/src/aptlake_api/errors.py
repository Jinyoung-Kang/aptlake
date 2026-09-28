"""RFC 9457 Problem Details + code + traceId."""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg
import redis.exceptions
from clickhouse_connect.driver import exceptions as ch_exc
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from .responses import OrjsonResponse

PROBLEM = "application/problem+json"
log = logging.getLogger("aptlake.api")

# 잠시 뒤 다시 하면 성공할 수 있는 ClickHouse 오류 — 500(버그)이 아니라 503 + Retry-After 로 알린다
TRANSIENT_CH_CODES = {
    159: "TIMEOUT_EXCEEDED",
    202: "TOO_MANY_SIMULTANEOUS_QUERIES",
    209: "SOCKET_TIMEOUT",
    210: "NETWORK_ERROR",
    241: "MEMORY_LIMIT_EXCEEDED",
}
_CH_CODE = re.compile(r"Code: (\d+)")
RETRY_AFTER_S = 2


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.status, self.code, self.title, self.detail = status, code, title, detail
        self.headers = headers or {}
        self.extra = extra or {}


def problem(
    request: Request,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> OrjsonResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "traceId": getattr(request.state, "trace_id", None),
    }
    if detail:
        body["detail"] = detail
    body.update(extra or {})
    return OrjsonResponse(body, status_code=status, headers=headers, media_type=PROBLEM)


async def api_error_handler(request: Request, exc: ApiError) -> OrjsonResponse:
    return problem(request, exc.status, exc.code, exc.title, exc.detail, exc.headers, exc.extra)


async def validation_handler(request: Request, exc: RequestValidationError) -> OrjsonResponse:
    errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
    return problem(request, 400, "INVALID_PARAMETER", "Invalid Parameter", extra={"errors": errors})


async def http_handler(request: Request, exc: StarletteHTTPException) -> OrjsonResponse:
    code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
    return problem(request, exc.status_code, code, str(exc.detail))


def _unavailable(request: Request, component: str, reason: str) -> OrjsonResponse:
    request.state.error = f"{component}: {reason}"  # 사용량 이벤트에 원인 분류로 남는다 (오류 로그 화면)
    log.warning(
        "upstream unavailable trace=%s component=%s reason=%s",
        getattr(request.state, "trace_id", "-"),
        component,
        reason,
    )
    return problem(
        request,
        503,
        "UPSTREAM_UNAVAILABLE",
        "Service Unavailable",
        f"{component} 가 일시적으로 요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.",
        {"Retry-After": str(RETRY_AFTER_S)},
        {"component": component, "retryable": True},
    )


def ch_transient(exc: ch_exc.Error) -> str | None:
    """일시 장애면 사유(예: TIMEOUT_EXCEEDED), 버그(문법·권한 등)면 None."""
    m = _CH_CODE.search(str(exc))
    code = int(m.group(1)) if m else None
    if (isinstance(exc, ch_exc.OperationalError) and code is None) or code in TRANSIENT_CH_CODES:
        return TRANSIENT_CH_CODES.get(code or 0, type(exc).__name__)
    return None


async def clickhouse_handler(request: Request, exc: ch_exc.Error) -> OrjsonResponse:
    """ClickHouse 오류: 연결 실패·메모리/동시성/시간 한도는 일시 장애(503), 그 밖(문법·권한 등)은 버그(500)."""
    reason = ch_transient(exc)
    if reason:
        return _unavailable(request, "서빙 DB(ClickHouse)", reason)
    m = _CH_CODE.search(str(exc))
    code = int(m.group(1)) if m else None
    log.error("clickhouse error trace=%s code=%s", getattr(request.state, "trace_id", "-"), code, exc_info=exc)
    request.state.error = f"ClickHouse code {code}" if code else type(exc).__name__
    return problem(request, 500, "INTERNAL", "Internal Server Error")


async def pg_handler(request: Request, exc: Exception) -> OrjsonResponse:
    return _unavailable(request, "운영 DB(PostgreSQL)", type(exc).__name__)


async def redis_handler(request: Request, exc: Exception) -> OrjsonResponse:
    return _unavailable(request, "캐시(Redis)", type(exc).__name__)


TRANSIENT_HANDLERS: list[tuple[type[Exception], Any]] = [
    (ch_exc.Error, clickhouse_handler),
    (psycopg.OperationalError, pg_handler),
    (PoolTimeout, pg_handler),
    (redis.exceptions.ConnectionError, redis_handler),
    (redis.exceptions.TimeoutError, redis_handler),
]


async def unhandled_handler(request: Request, exc: Exception) -> OrjsonResponse:
    # 내부 오류 내용은 응답에 싣지 않는다 (traceId 로 로그와 연결). 예외 클래스 이름만 사용량 이벤트에 남긴다 —
    # 메시지는 비밀값이 섞일 수 있어 남기지 않는다
    request.state.error = type(exc).__name__
    return problem(request, 500, "INTERNAL", "Internal Server Error")
