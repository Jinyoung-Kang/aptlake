"""요청 봉투 (순수 ASGI 미들웨어): 추적 ID · 보안 헤더 · 지표 · 사용량 기록 · 키 최근 사용 시각.

처음에는 `@app.middleware("http")`(Starlette BaseHTTPMiddleware)로 썼는데, 요청마다 태스크 그룹과 메모리
스트림을 만들고 응답 본문을 한 번 더 중계한다. 같은 일을 ASGI `send` 를 감싸는 방식으로 옮겼다 (동작 동일).
키 최근 사용 시각(DB 쓰기)은 응답을 보낸 뒤 백그라운드 작업으로 — 응답 지연에 포함되지 않게.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from prometheus_client import Counter, Histogram
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("aptlake.api")

REQ_LATENCY = Histogram(
    "aptlake_http_request_seconds",
    "request latency",
    ["app", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.08, 0.1, 0.15, 0.25, 0.5, 1, 2.5),
)
REQ_COUNT = Counter("aptlake_http_requests_total", "requests", ["app", "route", "status", "cache"])
_TRACE_RE = re.compile(r"^[0-9a-f]{16,32}$")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com"
)

_touched: dict[str, float] = {}
_background: set[asyncio.Task[Any]] = set()  # 참조를 잡아 두지 않으면 끝나기 전에 GC 될 수 있다


async def touch_key(app: Any, key_id: str) -> None:
    """last_used_at 은 키당 분당 최대 1회만 기록 (프로세스 내 선검사 → Redis NX → DB)."""
    now = time.monotonic()
    if _touched.get(key_id, 0) > now:
        return
    _touched[key_id] = now + 60
    if len(_touched) > 10_000:
        _touched.clear()
    res = app.state.res
    try:
        if await res.redis.set(f"al:touch:{key_id}", 1, ex=60, nx=True):
            async with res.pg.connection() as c:
                await c.execute("UPDATE api.api_key SET last_used_at = now() WHERE key_id = %s", (key_id,))
    except Exception:  # noqa: BLE001 — 부가 기록 실패가 요청에 영향을 주면 안 된다
        log.warning("touch_key failed key=%s", key_id[:4], exc_info=True)


class Envelope:
    def __init__(self, app: ASGIApp, name: str) -> None:
        self.app, self.name = app, name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        t0 = time.perf_counter()
        incoming = Headers(scope=scope).get("x-request-id", "")
        trace_id = incoming if _TRACE_RE.fullmatch(incoming) else uuid.uuid4().hex
        # request.state 는 scope["state"] 를 그대로 쓴다 → 의존성·핸들러가 쓴 값을 여기서 읽을 수 있다
        state: dict[str, Any] = scope.setdefault("state", {})
        state.update(trace_id=trace_id, rows=0, cache="-")
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Trace-Id"] = trace_id
                for k, v in SECURITY_HEADERS.items():
                    headers.setdefault(k, v)
                if getattr(scope.get("route"), "path", None) == "/docs":
                    headers["Content-Security-Policy"] = DOCS_CSP
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self._after(scope, state, status, time.perf_counter() - t0)

    def _after(self, scope: Scope, state: dict[str, Any], status: int, elapsed: float) -> None:
        route_path = getattr(scope.get("route"), "path", "unmatched")
        REQ_LATENCY.labels(self.name, route_path, str(status)).observe(elapsed)
        REQ_COUNT.labels(self.name, route_path, str(status), state.get("cache", "-")).inc()
        app = scope["app"]
        p = state.get("principal")
        if route_path.startswith("/v1"):
            app.state.usage.record(
                key_id=p.subject if p and p.kind == "key" else "anonymous",
                client_id=p.client_id if p else "unauthenticated",
                plan_id=p.plan.plan_id if p else "-",
                route=route_path,
                status=status,
                rows=state.get("rows", 0),
                latency_ms=int(elapsed * 1000),
                trace_id=state["trace_id"],
                error=str(state.get("error", ""))[:120] if status >= 500 else "",
            )
        key_id = state.get("touch_key")
        if key_id and status < 400:
            task = asyncio.create_task(touch_key(app, key_id))
            _background.add(task)
            task.add_done_callback(_background.discard)
