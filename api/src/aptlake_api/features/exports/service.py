"""대량 내려받기(Parquet) — 업무 규칙 (순수): 플랜·기간 검사, 클라이언트당 동시 작업 한도, 조회 응답 모양."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.values import iso

Row = dict[str, Any]
MAX_RUNNING = 2  # 클라이언트당 대기·진행 중 작업
MAX_SPAN_DAYS = 366 * 20


class ExportStore(Protocol):
    async def create_if_room(self, client_id: str, key_id: str, params_json: str, max_running: int) -> str | None: ...
    async def get(self, job_id: str, client_id: str) -> Row | None: ...


def check_request(allow_bulk: bool, start: dt.date, end: dt.date) -> None:
    if not allow_bulk:
        raise ApiError(403, "PLAN_NOT_ALLOWED", "Forbidden", "대량 내려받기는 pro 플랜만 가능합니다.")
    if end < start or (end - start).days > MAX_SPAN_DAYS:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range")


async def create(store: ExportStore, client_id: str, key_id: str, params_json: str) -> str:
    job_id = await store.create_if_room(client_id, key_id, params_json, MAX_RUNNING)
    if job_id is None:
        raise ApiError(429, "TOO_MANY_EXPORTS", "Too Many Requests", f"동시에 {MAX_RUNNING}개까지 가능합니다.")
    return job_id


async def status(
    store: ExportStore, job_id: str, client_id: str, *, presign: Callable[[str], str], url_ttl_s: int
) -> dict[str, Any]:
    job = await store.get(job_id, client_id)
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
        out["downloadUrl"] = presign(job["object_key"])
        out["expiresInSeconds"] = url_ttl_s
    if job["status"] == "failed":
        out["error"] = job["error"]
    return out
