"""앱 조립. 공개 API 와 내부(관리) API 는 서로 다른 프로세스·포트로 뜬다.

    공개   aptlake_api.main:public_app    :8610  /v1/*  (관리 라우트가 아예 없음)
    내부   aptlake_api.main:internal_app  :8611  /v1/admin/* + 내보내기 작업자 + 운영 지표
둘 다 127.0.0.1 에만 바인딩되고, 웹 화면은 nginx 를 통해 공개 API 만 프록시한다.
"""

from __future__ import annotations

import contextlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_client import REGISTRY, CollectorRegistry, multiprocess, start_http_server
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import exports, ops, routes_admin, routes_market, routes_ops, routes_public
from .core.auth import _SLIDING, load_plans
from .core.envelope import Envelope
from .core.errors import TRANSIENT_HANDLERS, api_error_handler, http_handler, unhandled_handler, validation_handler
from .core.http import DatasetVersion, assert_all_routes_scoped
from .core.problems import ApiError
from .core.resources import close_resources, open_resources
from .core.responses import OrjsonResponse
from .core.settings import settings
from .core.usage import UsageRecorder
from .features.regions.router import router as regions_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("aptlake.api")


def _build(name: str, internal: bool) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings()
        res = await open_resources(s)
        app.state.res = res
        app.state.plans = await load_plans(res)
        app.state.sliding = res.redis.register_script(_SLIDING)
        app.state.dsv = DatasetVersion()
        app.state.dagster = ops.DagsterClient(s.dagster_graphql_url)
        app.state.geo_cache = {}
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
        await app.state.dagster.aclose()
        await close_resources(res)

    app = FastAPI(
        title="AptLake 데이터 API" + (" (internal)" if internal else ""),
        version="1.0.0",
        description="전국 아파트 매매 실거래 — 정합성 검사를 통과한 데이터만 제공합니다. "
        "인증: `X-API-Key: al_live_<keyId>.<secret>` (없으면 anonymous 플랜). "
        "오류는 RFC 9457 Problem Details.",
        lifespan=lifespan,
        default_response_class=OrjsonResponse,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_handler)  # type: ignore[arg-type]
    for exc_type, handler in TRANSIENT_HANDLERS:  # DB·캐시 일시 장애 → 503 + Retry-After (500 은 버그에만)
        app.add_exception_handler(exc_type, handler)
    app.add_exception_handler(Exception, unhandled_handler)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> OrjsonResponse:
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
        return OrjsonResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)

    if internal:
        app.include_router(routes_admin.router)
    else:
        app.include_router(regions_router)
        app.include_router(routes_public.router)
        app.include_router(routes_market.router)
        app.include_router(routes_ops.router)
    # 큰 응답(경계 GeoJSON·수집 상태)은 압축. 비밀값이 섞이지 않는 응답이라 압축 부채널(BREACH) 우려 없음
    app.add_middleware(Envelope, name=name)  # 안쪽: 추적 ID·보안 헤더·지표·사용량
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    assert_all_routes_scoped(app)
    return app


def create_public_app() -> FastAPI:
    return _build("public", internal=False)


def create_internal_app() -> FastAPI:
    return _build("internal", internal=True)
