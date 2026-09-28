"""외부 API 응답 확인 — 오류 메시지에 인증키가 섞이지 않게.

공공데이터포털(serviceKey=)·R-ONE(KEY=)·V-World(key=)는 인증키를 URL 쿼리로 받는다.
httpx 의 raise_for_status() 는 예외 메시지에 URL 전체를 넣으므로, 그대로 쓰면 HTTP 오류 한 번에
키가 Dagster 실행 실패 이벤트(데이터베이스)에 평문으로 남는다 → 상태 코드와 경로만 담아 올린다.
"""

from __future__ import annotations

import httpx


class SourceHTTPError(Exception):
    def __init__(self, source: str, status: int, path: str):
        super().__init__(f"{source}: HTTP {status} ({path})")
        self.status = status


def checked(resp: httpx.Response, source: str) -> httpx.Response:
    if resp.is_error:
        raise SourceHTTPError(source, resp.status_code, resp.request.url.path)  # 쿼리(키)는 넣지 않는다
    return resp
