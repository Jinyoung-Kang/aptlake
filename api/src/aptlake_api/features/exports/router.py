"""대량 내려받기 API (HTTP 계층). bulk 스코프·pro 플랜."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field

from ...core.auth import Principal
from ...core.http import require_scope
from ...core.responses import OrjsonResponse
from ...core.settings import settings
from . import service, storage
from .repository import ExportRepository

router = APIRouter(prefix="/v1")


class ExportRequest(BaseModel):
    sggCd: str | None = Field(default=None, pattern=r"^\d{5}$")  # noqa: N815
    from_: dt.date = Field(alias="from")
    to: dt.date
    includeCancelled: bool = False  # noqa: N815


@router.post("/exports", status_code=202, summary="Parquet 내보내기 작업 생성 (bulk 스코프·pro 플랜)")
async def create_export(request: Request, body: ExportRequest, p: Principal = require_scope("bulk")) -> OrjsonResponse:
    service.check_request(p.plan.allow_bulk, body.from_, body.to)
    job_id = await service.create(
        ExportRepository(request.app.state.res.pg), p.client_id, p.subject, body.model_dump_json(by_alias=True)
    )
    return OrjsonResponse(
        {"jobId": job_id, "status": "queued"},
        status_code=202,
        headers={**p.limit_headers, "Location": f"/v1/exports/{job_id}"},
    )


@router.get("/exports/{jobId}", summary="내보내기 상태·만료 서명 URL")
async def get_export(
    request: Request,
    jobId: Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")],  # noqa: N803
    p: Principal = require_scope("bulk"),
) -> OrjsonResponse:
    body = await service.status(
        ExportRepository(request.app.state.res.pg),
        jobId,
        p.client_id,
        presign=storage.presign,
        url_ttl_s=settings().export_url_ttl_s,
    )
    return OrjsonResponse(body, headers=p.limit_headers)
