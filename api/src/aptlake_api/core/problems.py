"""업무 규칙이 쓰는 오류 형식 (RFC 9457 Problem Details 의 내용).

웹 프레임워크·DB 에 의존하지 않는다 — 서비스(업무 규칙) 계층이 import 해도 바깥 계층에 끌려가지 않도록.
HTTP 응답으로 바꾸는 일은 core.errors 의 처리기가 한다.
"""

from __future__ import annotations

from typing import Any


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
