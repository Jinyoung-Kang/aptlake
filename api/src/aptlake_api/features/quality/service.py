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
# 원천(ADR-050) → raw 보관 접두어. 수집 상태·예산 이름은 원천 이름 그대로('rtms' 매매·'rent' 전월세)
RAW_PREFIX = {"rtms": "rtms", "rent": "rtms_rent"}
SOURCES = tuple(RAW_PREFIX)


class QualityStore(Protocol):
    async def overview(self, source: str) -> dict[str, Any]: ...
    async def grid(self, ym_from: str, ym_to: str, sido: str | None, source: str) -> list[Row]: ...
    async def rollup(self, ym_from: str, ym_to: str, source: str) -> list[Row]: ...
    async def partition(self, sgg: str, ym: str, source: str) -> dict[str, Any] | None: ...


def check_span(start: dt.date, end: dt.date, limit: int) -> None:
    if end < start or months_between(start, end) > limit:
        raise ApiError(422, "RANGE_TOO_LARGE", "Range Too Large", f"최대 {limit}개월")


def schema_versions(rows: list[Row]) -> dict[str, int]:
    """적재된(LOADED·MERGED) 파티션 수를 마지막 적재 원본의 원천 스키마 판별로 — 매매 상세판 전환(ADR-049) 뒤 v1·v2 가
    섞인 정도. 키: 판 번호 · 'unrecorded' = 항목이 있는 원본이지만 판 기록(V013) 전에 적재 ·
    'empty' = 마지막 응답이 0건 — 항목이 없어 판을 판정할 수 없다 (판 기록을 세지 않는다: 빈 응답은 두 원천 모두 가장
    이른 판(1)으로 기록되지만 판정한 값이 아니고, 판 기록 전에 적재된 빈 응답은 원본이 같으면 다시 기록되지 않아 NULL 로 남는다)."""
    out: dict[str, int] = {}
    for r in rows:
        k = "empty" if r["empty"] else str(r["schema_ver"] or "unrecorded")
        out[k] = out.get(k, 0) + r["n"]
    return out


async def summary(store: QualityStore, source: str = "rtms") -> tuple[dict, int]:
    o = await store.overview(source)
    fresh, ver = o["fresh"], o["ver"]
    return {
        "source": source,
        "partitions": {r["status"]: r["n"] for r in o["status"]},
        "schemaVersions": schema_versions(o["schema"]),
        "freshness": {"lastFetchedAt": iso(fresh["fetched"]), "lastChangedAt": iso(fresh["changed"])},
        "dataset": None  # 이 원천의 마지막 발행
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
    store: QualityStore, from_: str, to: str, start: dt.date, end: dt.date, sido: str | None, source: str = "rtms"
) -> tuple[dict, int]:
    cells: dict[str, dict[str, Any]] = {}
    for r in await store.grid(start.strftime("%Y%m"), end.strftime("%Y%m"), sido, source):
        cells.setdefault(r["sgg_cd"], {})[r["deal_ym"]] = [r["status"], r["rows_last"]]
    return {"from": from_, "to": to, "cells": cells, "legend": ["status", "rows"]}, 0


async def rollup(
    store: QualityStore, from_: str, to: str, start: dt.date, end: dt.date, source: str = "rtms"
) -> tuple[dict, int]:
    cells: dict[str, dict[str, dict[str, int]]] = {}
    for r in await store.rollup(start.strftime("%Y%m"), end.strftime("%Y%m"), source):
        cells.setdefault(r["sido"], {}).setdefault(r["deal_ym"], {})[r["status"]] = r["n"]
    return {"from": from_, "to": to, "cells": cells}, 0


async def partition(store: QualityStore, sgg: str, deal_ym: str, source: str = "rtms") -> tuple[dict, int]:
    ym = deal_ym.replace("-", "")
    found = await store.partition(sgg, ym, source)
    if found is None:
        raise ApiError(404, "PARTITION_NOT_FOUND", "Not Found")
    part, checks, ver = found["part"], found["checks"], found["ver"]
    lineage = []
    if part["payload_sha256"]:
        lineage.append(f"s3://raw/{RAW_PREFIX[source]}/deal_ym={ym}/sgg_cd={sgg}/{part['payload_sha256']}/")
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


# ───────────────────────── 원천 반영 지연 (ADR-046) ─────────────────────────
LATENCY_MAX_DAYS = 366
# L3(수집→발행, 분) 분포 구간 상한 — 마지막 None 은 그 이상
L3_BUCKETS: list[int | None] = [15, 30, 60, 120, 360, 720, 1440, None]


class LatencyStore(Protocol):
    async def source_latency(self, start: dt.date, end: dt.date, sido: str | None) -> list[Row]: ...
    async def latency_since(self) -> dict[str, dt.date | None]: ...
    async def publish_lag(self, start: dt.date, end: dt.date, sido: str | None) -> Row: ...


def nearest_rank(dist: list[tuple[int, int]], q: float) -> int | None:
    """정렬된 (값, 건수) 분포의 q 분위 — 누적 건수가 q·N 이상이 되는 가장 작은 값 (PostgreSQL percentile_disc 와 같음)."""
    total = sum(n for _, n in dist)
    if not total:
        return None
    need, acc = q * total, 0
    for value, n in dist:
        acc += n
        if acc >= need:
            return value
    return dist[-1][0]


def _metric(rows: list[Row], since: dt.date | None) -> dict[str, Any]:
    dist: dict[int, int] = {}
    excluded: dict[str, int] = {}
    for r in rows:
        if r["reason"]:
            excluded[r["reason"]] = excluded.get(r["reason"], 0) + int(r["n"])
        else:
            dist[int(r["lag_days"])] = dist.get(int(r["lag_days"]), 0) + int(r["n"])
    d = sorted(dist.items())
    n = sum(dist.values())
    return {
        "unit": "day",
        "n": n,
        "p50": nearest_rank(d, 0.5),
        "p90": nearest_rank(d, 0.9),
        "p99": nearest_rank(d, 0.99),
        "distribution": [[k, v] for k, v in d],
        "excluded": dict(sorted(excluded.items())),
        "measuredSince": since.isoformat() if since else None,
    }


async def latency(store: LatencyStore, start: dt.date, end: dt.date, sido: str | None) -> tuple[dict, int]:
    if end < start or (end - start).days > LATENCY_MAX_DAYS:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range", f"기간은 {LATENCY_MAX_DAYS}일까지입니다.")
    rows = await store.source_latency(start, end, sido)
    since = await store.latency_since()
    l3 = await store.publish_lag(start, end, sido)
    metrics = {m: _metric([r for r in rows if r["metric"] == m], since.get(m)) for m in ("L1", "L2")}
    metrics["L3"] = {
        "unit": "minute",
        "n": int(l3["n"]),
        "p50": None if l3["p50"] is None else int(l3["p50"]),  # PostgreSQL numeric → 정수(분)
        "p90": None if l3["p90"] is None else int(l3["p90"]),
        "p99": None if l3["p99"] is None else int(l3["p99"]),
        "distribution": [[b, int(l3[f"b{i}"])] for i, b in enumerate(L3_BUCKETS)],
        "excluded": {},
        "measuredSince": l3["since"].isoformat() if l3["since"] else None,
    }
    return {"from": start.isoformat(), "to": end.isoformat(), "sido": sido, "metrics": metrics}, 0
