"""품질·계보: 신선도·파티션 상태·실패 검사 요약, 수집 격자, 시도 집계, 파티션 상세 — 업무 규칙 (순수)."""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.redact import redact
from ...core.values import iso, months_between

Row = dict[str, Any]
GRID_MAX_MONTHS = 60  # 전국 격자는 칸이 많다 (시군구 256 × 월)
SIDO_GRID_MAX_MONTHS = 240  # 시도 하나는 시군구가 많아야 47개


class QualityStore(Protocol):
    async def overview(self) -> dict[str, Any]: ...
    async def grid(self, ym_from: str, ym_to: str, sido: str | None) -> list[Row]: ...
    async def rollup(self, ym_from: str, ym_to: str) -> list[Row]: ...
    async def partition(self, sgg: str, ym: str) -> dict[str, Any] | None: ...


def check_span(start: dt.date, end: dt.date, limit: int) -> None:
    if end < start or months_between(start, end) > limit:
        raise ApiError(422, "RANGE_TOO_LARGE", "Range Too Large", f"최대 {limit}개월")


async def summary(store: QualityStore) -> tuple[dict, int]:
    o = await store.overview()
    fresh, ver = o["fresh"], o["ver"]
    return {
        "partitions": {r["status"]: r["n"] for r in o["status"]},
        "freshness": {"lastFetchedAt": iso(fresh["fetched"]), "lastChangedAt": iso(fresh["changed"])},
        "dataset": None
        if not ver
        else {
            "version": ver["version"],
            "publishedAt": iso(ver["published_at"]),
            "dataAsOf": iso(ver["data_as_of"]),
        },
        "failedChecks7d": [
            {
                "asset": f["asset"],
                "partition": f["partition"],
                "check": f["check_name"],
                "severity": f["severity"],
                "blocking": f["blocking"],
                "metric": f["metric"],
                "at": iso(f["at"]),
                "resolved": bool(f["resolved"]),  # 같은 검사가 이후 통과했으면 해결됨
            }
            for f in o["failed"]
        ],
        "apiBudget": [
            {
                "day": b["day"].isoformat(),
                "limit": b["limit_calls"],
                "used": b["used_calls"],
                "byPriority": b["by_priority"],
            }
            for b in o["budget"]
        ],
    }, 0


async def grid(
    store: QualityStore, from_: str, to: str, start: dt.date, end: dt.date, sido: str | None
) -> tuple[dict, int]:
    cells: dict[str, dict[str, Any]] = {}
    for r in await store.grid(start.strftime("%Y%m"), end.strftime("%Y%m"), sido):
        cells.setdefault(r["sgg_cd"], {})[r["deal_ym"]] = [r["status"], r["rows_last"]]
    return {"from": from_, "to": to, "cells": cells, "legend": ["status", "rows"]}, 0


async def rollup(store: QualityStore, from_: str, to: str, start: dt.date, end: dt.date) -> tuple[dict, int]:
    cells: dict[str, dict[str, dict[str, int]]] = {}
    for r in await store.rollup(start.strftime("%Y%m"), end.strftime("%Y%m")):
        cells.setdefault(r["sido"], {}).setdefault(r["deal_ym"], {})[r["status"]] = r["n"]
    return {"from": from_, "to": to, "cells": cells}, 0


async def partition(store: QualityStore, sgg: str, deal_ym: str) -> tuple[dict, int]:
    ym = deal_ym.replace("-", "")
    found = await store.partition(sgg, ym)
    if found is None:
        raise ApiError(404, "PARTITION_NOT_FOUND", "Not Found")
    part, checks, ver = found["part"], found["checks"], found["ver"]
    lineage = []
    if part["payload_sha256"]:
        lineage.append(f"s3://raw/rtms/deal_ym={ym}/sgg_cd={sgg}/{part['payload_sha256']}/")
    if ver:
        lineage += [f"{t}@snap {sid}" for t, sid in ver["snapshots"].items()]
        lineage.append(f"clickhouse ({ver['version']}, {iso(ver['published_at'])})")
    return {
        "partition": {
            "sggCd": sgg,
            "dealYm": deal_ym,
            "status": part["status"],
            "attempts": part["attempts"],
            "rows": part["rows_last"],
            "rowsPrev": part["rows_prev"],
            "observations": part["fetch_count"],
            "lastFetchedAt": iso(part["last_fetched_at"]),
            "lastChangedAt": iso(part["last_changed_at"]),
            "nextDueAt": iso(part["next_due_at"]),
            "lastError": redact(part["last_error"]) or None,  # 공개 응답 — 비밀값 가림 (오류 로그와 같은 규칙)
        },
        "checks": [
            {
                "asset": ch["asset"],
                "name": ch["check_name"],
                "passed": ch["passed"],
                "blocking": ch["blocking"],
                "severity": ch["severity"],
                "metric": ch["metric"],
                "at": iso(ch["at"]),
            }
            for ch in checks
        ],
        "lineage": lineage,
    }, 0
