"""앱 조립. 공개 API 와 내부(관리) API 는 서로 다른 프로세스·포트로 뜬다.

    공개   aptlake_api.main:public_app    :8610  /v1/*  (관리 라우트가 아예 없음)
    내부   aptlake_api.main:internal_app  :8611  /v1/admin/* + 내보내기 작업자 + 운영 지표
둘 다 127.0.0.1 에만 바인딩되고, 웹 화면은 nginx 를 통해 공개 API 만 프록시한다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram, multiprocess, start_http_server
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import exports, routes_admin, routes_public
from .auth import _SLIDING, load_plans
from .deps import DatasetVersion, assert_all_routes_scoped
from .errors import ApiError, api_error_handler, http_handler, unhandled_handler, validation_handler
from .resources import close_resources, open_resources
from .settings import settings
from .usage import UsageRecorder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
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
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com"
)


def _build(name: str, internal: bool) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings()
        res = await open_resources(s)
        app.state.res = res
        app.state.plans = await load_plans(res)
        app.state.sliding = res.redis.register_script(_SLIDING)
        app.state.dsv = DatasetVersion()
        app.state.usage = UsageRecorder(res.ch_usage, s.usage_flush_interval_s, s.usage_flush_max)
        app.state.usage.start()
        worker = exports.start(res) if internal else None
        # 지표: 워커가 여러 개면 multiprocess 모드로 모든 워커 값을 합쳐 한 포트에서 제공
        # (포트를 먼저 잡은 워커 하나가 서버 역할, 도커 네트워크 안에서만 — 호스트에 게시하지 않음)
        registry = REGISTRY
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        if internal:
            from .opsmetrics import OpsCollector

            registry.register(OpsCollector())
        with contextlib.suppress(OSError):
            start_http_server(s.metrics_port, registry=registry)
        yield
        if worker:
            await exports.stop(worker)
        await app.state.usage.stop()
        await close_resources(res)

    app = FastAPI(
        title="AptLake 데이터 API" + (" (internal)" if internal else ""),
        version="1.0.0",
        description="전국 아파트 매매 실거래 — 정합성 검사를 통과한 데이터만 제공합니다. "
        "인증: `X-API-Key: al_live_<keyId>.<secret>` (없으면 anonymous 플랜). "
        "오류는 RFC 9457 Problem Details.",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_handler)

    @app.middleware("http")
    async def envelope(request: Request, call_next):
        t0 = time.perf_counter()
        incoming = request.headers.get("x-request-id", "")
        request.state.trace_id = incoming if _TRACE_RE.fullmatch(incoming) else uuid.uuid4().hex
        request.state.rows = 0
        request.state.cache = "-"
        response = await call_next(request)
        elapsed = time.perf_counter() - t0
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        response.headers["X-Trace-Id"] = request.state.trace_id
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if route_path == "/docs":
            response.headers["Content-Security-Policy"] = _DOCS_CSP
        REQ_LATENCY.labels(name, route_path, str(response.status_code)).observe(elapsed)
        REQ_COUNT.labels(name, route_path, str(response.status_code), request.state.cache).inc()
        p = getattr(request.state, "principal", None)
        if route_path.startswith("/v1"):
            request.app.state.usage.record(
                key_id=p.subject if p and p.kind == "key" else "anonymous",
                client_id=p.client_id if p else "unauthenticated",
                plan_id=p.plan.plan_id if p else "-",
                route=route_path,
                status=response.status_code,
                rows=request.state.rows,
                latency_ms=int(elapsed * 1000),
                trace_id=request.state.trace_id,
            )
        key_id = getattr(request.state, "touch_key", None)
        if key_id and response.status_code < 400:
            await _touch_key(request, key_id)
        return response

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> ORJSONResponse:
        res = request.app.state.res
        checks = {}
        try:
            await res.redis.ping()
            checks["redis"] = "ok"
        except Exception:  # noqa: BLE001
            checks["redis"] = "fail"
        try:
            await res.ch.query("SELECT 1")
            checks["clickhouse"] = "ok"
        except Exception:  # noqa: BLE001
            checks["clickhouse"] = "fail"
        ok = all(v == "ok" for v in checks.values())
        return ORJSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)

    if internal:
        app.include_router(routes_admin.router)
    else:
        app.include_router(routes_public.router)
    assert_all_routes_scoped(app)
    return app


_touched: dict[str, float] = {}


async def _touch_key(request: Request, key_id: str) -> None:
    """last_used_at 은 키당 분당 최대 1회만 기록 (요청마다 DB·Redis 쓰기 방지: 프로세스 내 선검사 → Redis NX)."""
    now = time.monotonic()
    if _touched.get(key_id, 0) > now:
        return
    _touched[key_id] = now + 60
    res = request.app.state.res
    if await res.redis.set(f"al:touch:{key_id}", 1, ex=60, nx=True):
        async with res.pg.connection() as c:
            await c.execute("UPDATE api.api_key SET last_used_at = now() WHERE key_id = %s", (key_id,))


def create_public_app() -> FastAPI:
    return _build("public", internal=False)


def create_internal_app() -> FastAPI:
    return _build("internal", internal=True)
