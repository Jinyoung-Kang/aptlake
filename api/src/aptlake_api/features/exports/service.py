"""대량 내려받기(Parquet) — 업무 규칙 (순수): 플랜·기간 검사, 클라이언트당 동시 작업 한도, 버전 → 달 스냅숏 파일,
조회 응답 모양.

내보내기는 늘 달별 스냅숏 파일(ADR-044)에서 뽑는다 — 버전을 지정하든 안 하든 같은 경로라, 같은 버전·조건이면 같은 행이다.
버전 → 파일 규칙은 파이프라인 snapshots.files_for_version 과 같고, 같은 시험 사례표로 시험한다.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.values import iso

Row = dict[str, Any]
MAX_RUNNING = 2  # 클라이언트당 대기·진행 중 작업
MAX_SPAN_DAYS = 366 * 20
FILE_RETENTION = dt.timedelta(days=1)  # exports 버킷 수명 규칙(infra/minio/init.sh)과 같아야 한다 — 시험으로 확인


class SnapshotIndex(Protocol):
    async def versions(self) -> list[Row]: ...
    async def month_files(self) -> list[Row]: ...


class ExportStore(Protocol):
    async def create_if_room(
        self, client_id: str, key_id: str, params_json: str, dataset_version: str, max_running: int
    ) -> str | None: ...
    async def get(self, job_id: str, client_id: str) -> Row | None: ...


def check_request(allow_bulk: bool, start: dt.date, end: dt.date) -> None:
    if not allow_bulk:
        raise ApiError(403, "PLAN_NOT_ALLOWED", "Forbidden", "대량 내려받기는 pro 플랜만 가능합니다.")
    if end < start or (end - start).days > MAX_SPAN_DAYS:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range")


def _order(v: Row) -> tuple:
    return (v["published_at"], v["version"])


def files_for_version(versions: list[Row], files: list[Row], target: str) -> list[Row] | None:
    """버전 target 시점의 달 파일들(달 순). 기록 없는 버전이면 None, 기록 시작(기준선) 전 버전이면 []."""
    by_ver = {v["version"]: v for v in versions}
    t = by_ver.get(target)
    if t is None:
        return None
    pub = {str(v["publish_id"]): v for v in versions if v.get("publish_id")}
    best: dict[str, tuple[tuple, Row]] = {}
    for f in files:
        v = pub.get(str(f["publish_id"]))
        if v is None or _order(v) > _order(t):
            continue
        if f["deal_ym"] not in best or _order(v) > best[f["deal_ym"]][0]:
            best[f["deal_ym"]] = (_order(v), f)
    return [best[ym][1] for ym in sorted(best)]


def _usable(got: list[Row] | None) -> bool:
    return bool(got) and not any(f.get("deleted_at") for f in got or [])


def oldest_available(versions: list[Row], files: list[Row]) -> str | None:
    for v in sorted(versions, key=_order):
        if _usable(files_for_version(versions, files, v["version"])):
            return str(v["version"])
    return None


def resolve_snapshot(
    versions: list[Row], files: list[Row], requested: str | None, start: dt.date, end: dt.date
) -> tuple[str, list[Row]]:
    """(버전, 기간에 걸치는 달 파일). 지정하지 않으면 지금 최신 버전 — 요청 시점에 정해 작업에 저장한다."""
    if not versions:
        raise ApiError(
            410, "SNAPSHOT_EXPIRED", "Gone", "내보낼 수 있는 스냅숏이 아직 없습니다.", extra={"oldestAvailable": None}
        )
    target = requested or str(max(versions, key=_order)["version"])
    got = files_for_version(versions, files, target)
    if got is None:
        raise ApiError(404, "VERSION_NOT_FOUND", "Not Found", "발행 기록에 없는 데이터셋 버전입니다.")
    if not _usable(got):
        raise ApiError(
            410,
            "SNAPSHOT_EXPIRED",
            "Gone",
            "이 버전의 스냅숏은 보관 기간이 지났거나 기록 시작 전입니다.",
            extra={"oldestAvailable": oldest_available(versions, files)},
        )
    a, b = f"{start:%Y%m}", f"{end:%Y%m}"
    return target, [f for f in got if a <= f["deal_ym"] <= b]


async def create(store: ExportStore, client_id: str, key_id: str, params_json: str, dataset_version: str) -> str:
    job_id = await store.create_if_room(client_id, key_id, params_json, dataset_version, MAX_RUNNING)
    if job_id is None:
        raise ApiError(429, "TOO_MANY_EXPORTS", "Too Many Requests", f"동시에 {MAX_RUNNING}개까지 가능합니다.")
    return job_id


async def status(
    store: ExportStore,
    job_id: str,
    client_id: str,
    *,
    presign: Callable[[str], str],
    url_ttl_s: int,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    job = await store.get(job_id, client_id)
    if job is None:  # 다른 클라이언트의 작업도 404 (존재 여부 비노출)
        raise ApiError(404, "EXPORT_NOT_FOUND", "Not Found")
    out: dict[str, Any] = {
        "jobId": str(job["job_id"]),
        "status": job["status"],
        "rows": job["rows"],
        "datasetVersion": job.get("dataset_version"),
        "createdAt": iso(job["created_at"]),
        "finishedAt": iso(job["finished_at"]),
    }
    if job["status"] == "done" and job["object_key"]:
        # 결과 파일은 버킷 수명 규칙으로 지워진다 → 그 뒤에는 지워진 파일의 주소 대신 expired (D3)
        expires = (job["finished_at"] or job["created_at"]) + FILE_RETENTION
        out["expiresAt"] = iso(expires)
        out["sha256"] = job.get("sha256")
        if (now or dt.datetime.now(tz=dt.UTC)) >= expires:
            out["status"] = "expired"
        else:
            out["downloadUrl"] = presign(job["object_key"])
            out["expiresInSeconds"] = url_ttl_s
    if job["status"] == "failed":
        out["error"] = job["error"]
    return out
