"""RFC 9457 Problem Details + code + traceId."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .responses import OrjsonResponse

PROBLEM = "application/problem+json"


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


async def unhandled_handler(request: Request, exc: Exception) -> OrjsonResponse:
    # 내부 오류 내용은 응답에 싣지 않는다 (traceId 로 로그와 연결)
    return problem(request, 500, "INTERNAL", "Internal Server Error")
