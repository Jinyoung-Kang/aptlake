"""공개 데이터 API (/v1). 모든 ClickHouse 질의는 서버측 파라미터 바인딩만 쓴다 ({name:Type})."""

from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from .core.auth import Principal
from .core.clock import provisional
from .core.http import require_scope
from .core.problems import ApiError
from .core.responses import OrjsonResponse
from .core.settings import settings
from .core.values import iso

router = APIRouter(prefix="/v1")

SGG = Annotated[str, Path(pattern=r"^\d{5}$", description="시군구 코드 5자리 (법정동코드 앞 5자리)")]
YM_Q = r"^\d{4}-(0[1-9]|1[0-2])$"


def _ym_to_date(ym: str) -> dt.date:
    return dt.date(int(ym[:4]), int(ym[5:7]), 1)


def _months_between(a: dt.date, b: dt.date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) + 1


def _check_range(p: Principal, start: dt.date, end: dt.date) -> None:
    if end < start:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range", "to 는 from 이후여야 합니다.")
    limit = p.plan.max_range_months
    if limit is not None and _months_between(start, end) > limit:
        raise ApiError(422, "RANGE_EXCEEDS_PLAN", "Range Too Large", f"{p.plan.plan_id} 플랜은 최대 {limit}개월")


_provisional = provisional  # KST 기준 (deps)


async def _q(request: Request, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    res = await request.app.state.res.ch.query(sql, parameters=params)
    return list(res.named_results())


# ───────────────────────── 지역 ─────────────────────────


def _ts(v: dt.datetime | None) -> str | None:
    """ClickHouse DateTime64(…, 'UTC') 값은 naive 로 돌아오므로 UTC 를 명시한다."""
    if v is None:
        return None
    return (v if v.tzinfo else v.replace(tzinfo=dt.UTC)).isoformat()


def _round(v: float | None) -> float | None:
    return None if v is None or math.isnan(v) else round(v, 1)


# ───────────────────────── 내 사용량 ─────────────────────────


@router.get("/me/usage", summary="내 키(클라이언트) 일별 사용량")
async def my_usage(
    request: Request, days: Annotated[int, Query(ge=1, le=90)] = 30, p: Principal = require_scope("read")
) -> OrjsonResponse:
    if p.kind != "key":
        raise ApiError(401, "API_KEY_REQUIRED", "Unauthorized", "사용량 조회에는 API 키가 필요합니다.")
    # 필터는 항상 인증된 키의 client_id — 요청 파라미터로 다른 클라이언트를 지정할 방법이 없다
    rows = await _q(
        request,
        """
        SELECT toDate(at, 'Asia/Seoul') AS day, count() AS requests, sum(rows) AS rows,
               countIf(status >= 400) AS errors, quantile(0.95)(latency_ms) AS p95_ms
        FROM usage_event
        WHERE client_id = {c:String} AND at >= now() - toIntervalDay({d:UInt16})
        GROUP BY day ORDER BY day""",
        {"c": p.client_id, "d": days},
    )
    return OrjsonResponse(
        {
            "clientId": p.client_id,
            "plan": p.plan.plan_id,
            "limits": {"rpm": p.plan.rpm, "dailyRows": p.plan.daily_rows},
            "items": [
                {
                    "day": r["day"].isoformat(),
                    "requests": r["requests"],
                    "rows": r["rows"],
                    "errors": r["errors"],
                    "p95Ms": r["p95_ms"],
                }
                for r in rows
            ],
        },
        headers=p.limit_headers,
    )


# ───────────────────────── 대량 내려받기 ─────────────────────────


class ExportRequest(BaseModel):
    sggCd: str | None = Field(default=None, pattern=r"^\d{5}$")  # noqa: N815
    from_: dt.date = Field(alias="from")
    to: dt.date
    includeCancelled: bool = False  # noqa: N815


@router.post("/exports", status_code=202, summary="Parquet 내보내기 작업 생성 (bulk 스코프·pro 플랜)")
async def create_export(request: Request, body: ExportRequest, p: Principal = require_scope("bulk")) -> OrjsonResponse:
    if not p.plan.allow_bulk:
        raise ApiError(403, "PLAN_NOT_ALLOWED", "Forbidden", "대량 내려받기는 pro 플랜만 가능합니다.")
    if body.to < body.from_ or (body.to - body.from_).days > 366 * 20:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range")
    async with request.app.state.res.pg.connection() as c, c.transaction():
        # '확인 후 추가'를 클라이언트별 잠금 안에서 — 동시에 여러 요청이 와도 진행 중 2개를 넘지 않게
        await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"export:{p.client_id}",))
        running = await (
            await c.execute(
                "SELECT count(*) AS n FROM api.export_job WHERE client_id=%s AND status IN ('queued','running')",
                (p.client_id,),
            )
        ).fetchone()
        if running["n"] >= 2:
            raise ApiError(429, "TOO_MANY_EXPORTS", "Too Many Requests", "동시에 2개까지 가능합니다.")
        row = await (
            await c.execute(
                """INSERT INTO api.export_job (client_id, key_id, params) VALUES (%s, %s, %s) RETURNING job_id""",
                (p.client_id, p.subject, body.model_dump_json(by_alias=True)),
            )
        ).fetchone()
    return OrjsonResponse(
        {"jobId": str(row["job_id"]), "status": "queued"},
        status_code=202,
        headers={**p.limit_headers, "Location": f"/v1/exports/{row['job_id']}"},
    )


@router.get("/exports/{jobId}", summary="내보내기 상태·만료 서명 URL")
async def get_export(
    request: Request,
    jobId: Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")],  # noqa: N803
    p: Principal = require_scope("bulk"),
) -> OrjsonResponse:
    async with request.app.state.res.pg.connection() as c:
        job = await (
            await c.execute(
                """SELECT job_id, status, rows, error, object_key, created_at, finished_at FROM api.export_job
               WHERE job_id = %s AND client_id = %s""",
                (jobId, p.client_id),
            )
        ).fetchone()
    if job is None:  # 다른 클라이언트의 작업도 404 (존재 여부 비노출)
        raise ApiError(404, "EXPORT_NOT_FOUND", "Not Found")
    out: dict[str, Any] = {
        "jobId": str(job["job_id"]),
        "status": job["status"],
        "rows": job["rows"],
        "createdAt": iso(job["created_at"]),
        "finishedAt": iso(job["finished_at"]),
    }
    if job["status"] == "done" and job["object_key"]:
        from .exports import presign

        out["downloadUrl"] = presign(job["object_key"])
        out["expiresInSeconds"] = settings().export_url_ttl_s
    if job["status"] == "failed":
        out["error"] = job["error"]
    return OrjsonResponse(out, headers=p.limit_headers)
