"""공개 데이터 API (/v1). 모든 ClickHouse 질의는 서버측 파라미터 바인딩만 쓴다 ({name:Type})."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import ops
from .core import cursor as cursor_mod
from .core.auth import Principal
from .core.clock import provisional
from .core.http import DISCLAIMER, require_scope, respond
from .core.problems import ApiError
from .core.responses import OrjsonResponse
from .core.settings import settings

router = APIRouter(prefix="/v1")

SGG = Annotated[str, Path(pattern=r"^\d{5}$", description="시군구 코드 5자리 (법정동코드 앞 5자리)")]
YM_Q = r"^\d{4}-(0[1-9]|1[0-2])$"
TRADE_ID = re.compile(r"^(\d{5})-(\d{6})-[0-9a-f]{16}-\d{1,4}$")
COMPLEX_KEY = r"^c_[0-9a-f]{20}$"


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


# ───────────────────────── 거래 ─────────────────────────

TRADE_COLS = """trade_id, deal_date, complex_key, apt_nm, umd_nm, jibun, area_m2, floor, price_manwon, ppm2,
                is_cancelled, cancel_date, registered_date, apt_dong, deal_kind, seller_type, buyer_type,
                build_year, is_outlier, version, missing_since"""


def _trade(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "tradeId": r["trade_id"],
        "dealDate": r["deal_date"].isoformat(),
        "complex": {
            "complexKey": r["complex_key"],
            "aptName": r["apt_nm"],
            "umdName": r["umd_nm"],
            "jibun": r["jibun"] or None,
            "buildYear": r["build_year"],
        },
        "priceManwon": r["price_manwon"],
        "areaM2": float(r["area_m2"]),
        "floor": r["floor"],
        "pricePerM2": round(r["ppm2"], 1) if r["ppm2"] else None,
        "cancelled": bool(r["is_cancelled"]),
        "cancelDate": r["cancel_date"].isoformat() if r["cancel_date"] else None,
        "registeredDate": r["registered_date"].isoformat() if r["registered_date"] else None,
        "aptDong": r["apt_dong"] or None,
        "dealKind": r["deal_kind"] or None,
        "sellerType": r["seller_type"] or None,
        "buyerType": r["buyer_type"] or None,
        "outlier": bool(r["is_outlier"]),
        "version": r["version"],
        "missingSince": _ts(r["missing_since"]),
    }


@router.get("/trades", summary="거래 목록 (커서 페이지, 최신 계약일 순)")
async def trades(
    request: Request,
    sggCd: Annotated[str, Query(pattern=r"^\d{5}$")],  # noqa: N803
    from_: Annotated[dt.date, Query(alias="from")],
    to: dt.date,
    minArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    maxArea: Annotated[float | None, Query(ge=0, le=1000)] = None,  # noqa: N803
    includeCancelled: bool = False,  # noqa: N803
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    cursor: Annotated[str | None, Query(max_length=400)] = None,
    p: Principal = require_scope("read"),
) -> Response:
    _check_range(p, from_, to)
    if limit > p.plan.max_page_size:
        raise ApiError(
            422,
            "PAGE_SIZE_EXCEEDS_PLAN",
            "Page Too Large",
            f"{p.plan.plan_id} 플랜의 limit 상한은 {p.plan.max_page_size}",
        )
    query = {
        "sgg": sggCd,
        "a": from_.isoformat(),
        "b": to.isoformat(),
        "min": minArea,
        "max": maxArea,
        "c": includeCancelled,
        "l": limit,
    }
    fp = cursor_mod.query_fingerprint(query)
    key = settings().cursor_signing_key.get_secret_value().encode()
    pos = None
    if cursor:
        pos = cursor_mod.decode(key, cursor, fp)
        if pos is None:
            raise ApiError(400, "INVALID_CURSOR", "Invalid Cursor", "커서 서명이 맞지 않거나 다른 질의의 커서입니다.")

    async def compute():
        where = ["sgg_cd = {sgg:String}", "deal_date BETWEEN {a:Date} AND {b:Date}"]
        params: dict[str, Any] = {"sgg": sggCd, "a": from_, "b": to, "lim": limit + 1}
        if minArea is not None:
            where.append("area_m2 >= {min:Float64}")
            params["min"] = minArea
        if maxArea is not None:
            where.append("area_m2 <= {max:Float64}")
            params["max"] = maxArea
        if not includeCancelled:
            where.append("is_cancelled = 0")
        if pos:
            where.append("(deal_date, trade_id) < ({cd:Date}, {ck:String})")
            params["cd"] = dt.date.fromisoformat(pos["d"])
            params["ck"] = pos["k"]
        rows = await _q(
            request,
            f"""SELECT {TRADE_COLS} FROM trade_current WHERE {" AND ".join(where)}
                                    ORDER BY deal_date DESC, trade_id DESC LIMIT {{lim:UInt32}}""",
            params,
        )
        summary = None
        if not pos:
            # 첫 페이지: 조건 전체 요약 (불러온 페이지가 아니라 전체 조건 기준)
            base_where = [w for w in where if not w.startswith("(deal_date, trade_id)")]
            agg = await _q(
                request,
                f"""SELECT count() AS n, countIf(is_cancelled = 1) AS cancelled,
                           quantileExactInclusiveIf(0.5)(ppm2, is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0) AS med,
                           quantileExactInclusiveIf(0.5)(price_manwon, is_cancelled = 0) AS med_price
                    FROM trade_current WHERE {" AND ".join(base_where)}""",
                params,
            )
            a0 = agg[0]
            summary = {
                "count": a0["n"],
                "cancelled": a0["cancelled"],
                "medianPpm2": _round(a0["med"]),
                # 조건의 거래가 모두 해제면 유효 거래가 없어 중위가가 NaN — round(NaN) 은 예외(500)라 '없음'으로
                "medianPrice": None if a0["n"] == 0 or _round(a0["med_price"]) is None else round(a0["med_price"]),
            }
        has_more = len(rows) > limit
        rows = rows[:limit]
        nxt = None
        if has_more and rows:
            last = rows[-1]
            nxt = cursor_mod.encode(key, {"d": last["deal_date"].isoformat(), "k": last["trade_id"]}, fp)
        return {
            "items": [_trade(r) for r in rows],
            "summary": summary,
            "page": {"limit": limit, "nextCursor": nxt},
            "disclaimer": DISCLAIMER,
        }, len(rows)

    return await respond(request, "trades", {**query, "cursor": cursor}, compute)


@router.get("/trades/{tradeId}/history", summary="거래 버전 이력 (SCD2)")
async def trade_history(
    request: Request,
    tradeId: Annotated[str, Path(max_length=40)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    m = TRADE_ID.fullmatch(tradeId)
    if not m:
        raise ApiError(400, "INVALID_TRADE_ID", "Invalid Trade Id")
    month = dt.date(int(m.group(2)[:4]), int(m.group(2)[4:]), 1)

    async def compute():
        rows = await _q(
            request,
            """
            SELECT version, valid_from, valid_to, is_current, is_cancelled, cancel_date, registered_date,
                   apt_dong, deal_kind, seller_type, buyer_type
            FROM trade_version
            WHERE trade_id = {id:String} AND toYYYYMM(deal_date) = {ym:UInt32}
            ORDER BY valid_from""",
            {"id": tradeId, "ym": month.year * 100 + month.month},
        )
        if not rows:
            raise ApiError(404, "TRADE_NOT_FOUND", "Not Found")
        tracked = {
            "is_cancelled": "cancelled",
            "cancel_date": "cancelDate",
            "registered_date": "registeredDate",
            "apt_dong": "aptDong",
            "deal_kind": "dealKind",
            "seller_type": "sellerType",
            "buyer_type": "buyerType",
        }
        versions, prev = [], None
        for r in rows:
            changes: list[dict[str, Any]] = []
            if prev is not None:
                for col, name in tracked.items():
                    if prev[col] != r[col]:
                        changes.append({"field": name, "from": _json(prev[col]), "to": _json(r[col])})
            versions.append(
                {
                    "version": r["version"],
                    "validFrom": _ts(r["valid_from"]),
                    "validTo": _ts(r["valid_to"]),
                    "current": bool(r["is_current"]),
                    "changes": changes,
                    "state": {name: _json(r[col]) for col, name in tracked.items()},
                }
            )
            prev = r
        return {"tradeId": tradeId, "versions": versions}, len(versions)

    return await respond(request, "trade_history", {"id": tradeId}, compute)


def _json(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return _ts(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, int) and v in (0, 1) and not isinstance(v, bool):
        return bool(v)
    return v or None


@router.get("/complexes/{complexKey}", summary="단지 정보 + 최근 거래")
async def complex_detail(
    request: Request,
    complexKey: Annotated[str, Path(pattern=COMPLEX_KEY)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    async def compute():
        c = await _q(
            request,
            """SELECT complex_key, sgg_cd, umd_nm, jibun, apt_nm, build_year, land_leasehold,
                                        first_seen, trades FROM complex WHERE complex_key = {k:String}""",
            {"k": complexKey},
        )
        if not c:
            raise ApiError(404, "COMPLEX_NOT_FOUND", "Not Found")
        cx = c[0]
        recent = await _q(
            request,
            f"""SELECT {TRADE_COLS} FROM trade_current
                                       WHERE sgg_cd = {{sgg:String}} AND complex_key = {{k:String}}
                                       ORDER BY deal_date DESC, trade_id DESC LIMIT 20""",
            {"sgg": cx["sgg_cd"], "k": complexKey},
        )
        history = await _q(
            request,
            """SELECT deal_date, toFloat64(area_m2) AS area, floor, price_manwon, ppm2, is_cancelled, is_outlier
               FROM trade_current WHERE sgg_cd = {sgg:String} AND complex_key = {k:String}
               ORDER BY deal_date LIMIT 1000""",
            {"sgg": cx["sgg_cd"], "k": complexKey},
        )
        return {
            "complex": {
                "complexKey": cx["complex_key"],
                "sggCd": cx["sgg_cd"],
                "umdName": cx["umd_nm"],
                "jibun": cx["jibun"] or None,
                "aptName": cx["apt_nm"],
                "buildYear": cx["build_year"],
                "landLeasehold": bool(cx["land_leasehold"]),
                "firstSeen": cx["first_seen"].isoformat(),
                "validTrades": cx["trades"],
            },
            "recentTrades": [_trade(r) for r in recent],
            "history": [
                [
                    h["deal_date"].isoformat(),
                    round(h["area"], 2),
                    h["floor"],
                    h["price_manwon"],
                    round(h["ppm2"], 1),
                    h["is_cancelled"],
                    h["is_outlier"],
                ]
                for h in history
            ],
            "historyFields": ["dealDate", "areaM2", "floor", "priceManwon", "pricePerM2", "cancelled", "outlier"],
        }, 1 + len(recent) + len(history)

    return await respond(request, "complex", {"k": complexKey}, compute)


# ───────────────────────── 지수 ─────────────────────────


@router.get("/index", summary="자체 지수 시계열 + R-ONE 대비 검증 지표")
async def price_index(
    request: Request,
    regionId: Annotated[str, Query(pattern=r"^\d{2}$", description="시도 2자리, 전국 00")],  # noqa: N803
    method: Annotated[str, Query(pattern=r"^HEDONIC_TD_v1$")] = "HEDONIC_TD_v1",
    p: Principal = require_scope("read"),
) -> Response:
    async def compute():
        series = await _q(
            request,
            """SELECT period, index_value, ci_low, ci_high, n_obs, model_ver FROM price_index
                                      WHERE region_id = {r:String} AND method = {m:String} ORDER BY period""",
            {"r": regionId, "m": method},
        )
        ref = await _q(
            request,
            """SELECT period, value, source FROM index_reference
                                   WHERE region_id = {r:String} ORDER BY period""",
            {"r": regionId},
        )
        val = await _q(
            request,
            """SELECT reference, corr_mom, direction_match, n_months, window_from, window_to
                                   FROM index_validation WHERE region_id = {r:String} AND method = {m:String}""",
            {"r": regionId, "m": method},
        )
        if not series:
            raise ApiError(404, "INDEX_NOT_FOUND", "Not Found", f"{regionId} 지역 지수가 아직 없습니다.")
        v = val[0] if val else None
        return {
            "regionId": regionId,
            "method": method,
            "base": f"{series[0]['period']:%Y-%m}=100",
            "series": [
                {
                    "period": f"{r['period']:%Y-%m}",
                    "value": round(r["index_value"], 2),
                    "ciLow": round(r["ci_low"], 2),
                    "ciHigh": round(r["ci_high"], 2),
                    "nObs": r["n_obs"],
                    **({"provisional": True} if _provisional(r["period"]) else {}),
                }
                for r in series
            ],
            "reference": {
                "source": ref[0]["source"] if ref else None,
                "series": [{"period": f"{r['period']:%Y-%m}", "value": round(r["value"], 3)} for r in ref],
            },
            "validation": None
            if v is None
            else {
                "reference": v["reference"],
                "corrMoM": v["corr_mom"],
                "directionMatch": v["direction_match"],
                "months": v["n_months"],
                "window": f"{v['window_from']:%Y-%m}~{v['window_to']:%Y-%m}",
            },
            "disclaimer": "자체 산출 실험 지수이며 공식 통계가 아닙니다. " + DISCLAIMER,
        }, 0  # 집계 응답

    return await respond(request, "index", {"r": regionId, "m": method}, compute)


# ───────────────────────── 품질·계보 ─────────────────────────


@router.get("/quality/summary", summary="전체 신선도·파티션 상태·최근 실패 검사")
async def quality_summary(request: Request, p: Principal = require_scope("read")) -> Response:
    async def compute():
        async with request.app.state.res.pg.connection() as c:
            status = await (
                await c.execute("SELECT status, count(*) AS n FROM ops.ingest_partition GROUP BY status")
            ).fetchall()
            fresh = await (
                await c.execute(
                    "SELECT max(last_fetched_at) AS fetched, max(last_changed_at) AS changed FROM ops.ingest_partition"
                )
            ).fetchone()
            ver = await (
                await c.execute(
                    """SELECT version, published_at, data_as_of FROM ops.dataset_version
                       ORDER BY published_at DESC LIMIT 1"""
                )
            ).fetchone()
            failed = await (
                await c.execute(
                    """SELECT d.asset, d.partition, d.check_name, d.severity, d.blocking, d.metric, d.at,
                              EXISTS (SELECT 1 FROM ops.dq_result x
                                      WHERE x.asset = d.asset AND x.partition IS NOT DISTINCT FROM d.partition
                                        AND x.check_name = d.check_name AND x.at > d.at AND x.passed) AS resolved
                       FROM ops.dq_result d
                       WHERE NOT d.passed AND d.at > now() - interval '7 days' ORDER BY d.at DESC LIMIT 200"""
                )
            ).fetchall()
            budget = await (
                await c.execute(
                    """SELECT day, limit_calls, used_calls, by_priority FROM ops.api_budget
                   WHERE source = 'rtms' ORDER BY day DESC LIMIT 7"""
                )
            ).fetchall()
        return {
            "partitions": {r["status"]: r["n"] for r in status},
            "freshness": {"lastFetchedAt": _iso(fresh["fetched"]), "lastChangedAt": _iso(fresh["changed"])},
            "dataset": None
            if not ver
            else {
                "version": ver["version"],
                "publishedAt": _iso(ver["published_at"]),
                "dataAsOf": _iso(ver["data_as_of"]),
            },
            "failedChecks7d": [
                {
                    "asset": f["asset"],
                    "partition": f["partition"],
                    "check": f["check_name"],
                    "severity": f["severity"],
                    "blocking": f["blocking"],
                    "metric": f["metric"],
                    "at": _iso(f["at"]),
                    "resolved": bool(f["resolved"]),  # 같은 검사가 이후 통과했으면 해결됨
                }
                for f in failed
            ],
            "apiBudget": [
                {
                    "day": b["day"].isoformat(),
                    "limit": b["limit_calls"],
                    "used": b["used_calls"],
                    "byPriority": b["by_priority"],
                }
                for b in budget
            ],
        }, 0

    return await respond(request, "quality_summary", {}, compute, cache=False)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


@router.get("/quality/partitions", summary="파티션 상태 격자 (시군구 × 계약월) — 품질 히트맵용")
async def quality_grid(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    sido: Annotated[str | None, Query(pattern=r"^\d{2}$", description="시도 2자리 — 지정하면 그 시도 시군구만")] = None,
    p: Principal = require_scope("read"),
) -> Response:
    start, end = _ym_to_date(from_), _ym_to_date(to)
    # 전국 격자는 칸이 많아(시군구 256 × 월) 60개월까지, 시도 하나는 시군구가 많아야 47개라 240개월까지
    limit = 240 if sido else 60
    if end < start or _months_between(start, end) > limit:
        raise ApiError(422, "RANGE_TOO_LARGE", "Range Too Large", f"최대 {limit}개월")

    async def compute():
        async with request.app.state.res.pg.connection() as c:
            rows = await (
                await c.execute(
                    """SELECT p.sgg_cd, p.deal_ym, p.status, p.rows_last FROM ops.ingest_partition p
                   WHERE p.deal_ym BETWEEN %s AND %s AND (%s::text IS NULL OR left(p.sgg_cd, 2) = %s)
                   ORDER BY p.sgg_cd, p.deal_ym""",
                    (start.strftime("%Y%m"), end.strftime("%Y%m"), sido, sido),
                )
            ).fetchall()
        grid: dict[str, dict[str, Any]] = {}
        for r in rows:
            grid.setdefault(r["sgg_cd"], {})[r["deal_ym"]] = [r["status"], r["rows_last"]]
        return {"from": from_, "to": to, "cells": grid, "legend": ["status", "rows"]}, 0

    return await respond(request, "quality_grid", {"a": from_, "b": to, "s": sido}, compute, cache=False)


@router.get("/quality/rollup", summary="시도 × 계약월 수집 완결도 (파티션 상태별 개수)")
async def quality_rollup(
    request: Request,
    from_: Annotated[str, Query(alias="from", pattern=YM_Q)],
    to: Annotated[str, Query(pattern=YM_Q)],
    p: Principal = require_scope("read"),
) -> Response:
    start, end = _ym_to_date(from_), _ym_to_date(to)
    if end < start or _months_between(start, end) > 240:
        raise ApiError(422, "RANGE_TOO_LARGE", "Range Too Large", "최대 240개월")

    async def compute():
        async with request.app.state.res.pg.connection() as c:
            rows = await (
                await c.execute(
                    """SELECT left(sgg_cd, 2) AS sido, deal_ym, status, count(*) AS n FROM ops.ingest_partition
                       WHERE deal_ym BETWEEN %s AND %s GROUP BY 1, 2, 3 ORDER BY 1, 2""",
                    (start.strftime("%Y%m"), end.strftime("%Y%m")),
                )
            ).fetchall()
        cells: dict[str, dict[str, dict[str, int]]] = {}
        for r in rows:
            cells.setdefault(r["sido"], {}).setdefault(r["deal_ym"], {})[r["status"]] = r["n"]
        return {"from": from_, "to": to, "cells": cells}, 0

    return await respond(request, "quality_rollup", {"a": from_, "b": to}, compute, cache=False)


@router.get("/quality/partitions/{sggCd}/{dealYm}", summary="파티션 품질 검사 결과·계보")
async def quality_partition(
    request: Request,
    sggCd: SGG,  # noqa: N803
    dealYm: Annotated[str, Path(pattern=YM_Q)],  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    ym = dealYm.replace("-", "")

    async def compute():
        async with request.app.state.res.pg.connection() as c:
            part = await (
                await c.execute(
                    """SELECT status, attempts, rows_last, rows_prev, payload_sha256, last_ingest_id, last_fetched_at,
                          last_changed_at, fetch_count, next_due_at, last_error
                   FROM ops.ingest_partition WHERE sgg_cd=%s AND deal_ym=%s""",
                    (sggCd, ym),
                )
            ).fetchone()
            if part is None:
                raise ApiError(404, "PARTITION_NOT_FOUND", "Not Found")
            checks = await (
                await c.execute(
                    """SELECT DISTINCT ON (asset, check_name) asset, check_name, passed, severity, blocking, metric, at
                   FROM ops.dq_result WHERE partition = %s OR partition = %s
                   ORDER BY asset, check_name, at DESC""",
                    (f"{sggCd}/{ym}", ym),
                )
            ).fetchall()
            ver = await (
                await c.execute(
                    """SELECT version, published_at, snapshots FROM ops.dataset_version WHERE %s = ANY(partitions)
                   ORDER BY published_at DESC LIMIT 1""",
                    (ym,),
                )
            ).fetchone()
        lineage = []
        if part["payload_sha256"]:
            lineage.append(f"s3://raw/rtms/deal_ym={ym}/sgg_cd={sggCd}/{part['payload_sha256']}/")
        if ver:
            lineage += [f"{t}@snap {sid}" for t, sid in ver["snapshots"].items()]
            lineage.append(f"clickhouse ({ver['version']}, {_iso(ver['published_at'])})")
        return {
            "partition": {
                "sggCd": sggCd,
                "dealYm": dealYm,
                "status": part["status"],
                "attempts": part["attempts"],
                "rows": part["rows_last"],
                "rowsPrev": part["rows_prev"],
                "observations": part["fetch_count"],
                "lastFetchedAt": _iso(part["last_fetched_at"]),
                "lastChangedAt": _iso(part["last_changed_at"]),
                "nextDueAt": _iso(part["next_due_at"]),
                "lastError": ops.redact(part["last_error"]) or None,  # 공개 응답 — 비밀값 가림 (오류 로그와 같은 규칙)
            },
            "checks": [
                {
                    "asset": ch["asset"],
                    "name": ch["check_name"],
                    "passed": ch["passed"],
                    "blocking": ch["blocking"],
                    "severity": ch["severity"],
                    "metric": ch["metric"],
                    "at": _iso(ch["at"]),
                }
                for ch in checks
            ],
            "lineage": lineage,
        }, 0

    return await respond(request, "quality_partition", {"s": sggCd, "m": ym}, compute, cache=False)


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
        "createdAt": _iso(job["created_at"]),
        "finishedAt": _iso(job["finished_at"]),
    }
    if job["status"] == "done" and job["object_key"]:
        from .exports import presign

        out["downloadUrl"] = presign(job["object_key"])
        out["expiresInSeconds"] = settings().export_url_ttl_s
    if job["status"] == "failed":
        out["error"] = job["error"]
    return OrjsonResponse(out, headers=p.limit_headers)
