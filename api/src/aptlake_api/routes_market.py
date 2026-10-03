"""시장 개요 · 지도 · 검색 · 지역 상세(가격 분포·단지) — 웹 화면용 집계 API.

집계 응답(시군구·시도 통계, 순위, 분포 구간)은 원자료를 내보내지 않으므로 일일 '데이터 행' 한도에 넣지 않고,
거래 단위 점(산점도)은 넣는다. 모든 ClickHouse 질의는 서버측 파라미터 바인딩.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import Response

from .core.auth import Principal
from .core.clock import provisional
from .core.http import DISCLAIMER, require_scope, respond
from .core.problems import ApiError

router = APIRouter(prefix="/v1")
YM_Q = r"^\d{4}-(0[1-9]|1[0-2])$"
SGG = Annotated[str, Path(pattern=r"^\d{5}$")]
MIN_SAMPLE_FOR_CHANGE = 30  # 변화율 순위: 두 달 모두 표본 30건 이상인 시군구만 (작은 표본의 착시 방지)


def _d(ym: str) -> dt.date:
    return dt.date(int(ym[:4]), int(ym[5:7]), 1)


def _add(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    return dt.date(d.year + m // 12, m % 12 + 1, 1)


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 2)


def _r(v: float | None, n: int = 0) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)


async def _q(request: Request, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return list((await request.app.state.res.ch.query(sql, parameters=params)).named_results())


async def _month_bounds(request: Request) -> tuple[dt.date | None, dt.date | None]:
    rows = await _q(request, "SELECT min(month) AS a, max(month) AS b FROM region_month", {})
    return (rows[0]["a"], rows[0]["b"]) if rows and rows[0]["b"] else (None, None)


def _default_month(latest: dt.date) -> dt.date:
    """기본 기준월 = 잠정이 아닌 가장 최근 달 (최근 달은 신고가 계속 들어와 비교가 왜곡된다)."""
    m = latest
    for _ in range(6):
        if not provisional(m):
            return m
        m = _add(m, -1)
    return latest


# ───────────────────────── 시장 개요 ─────────────────────────


@router.get("/market/overview", summary="전국·시도·시군구 한 달 요약 + 순위 (지도·표용)")
async def overview(
    request: Request, ym: Annotated[str | None, Query(pattern=YM_Q)] = None, p: Principal = require_scope("read")
) -> Response:
    async def compute():
        lo, hi = await _month_bounds(request)
        if lo is None or hi is None:
            raise ApiError(404, "NO_DATA", "Not Found", "아직 발행된 데이터가 없습니다.")
        month = _d(ym) if ym else _default_month(hi)
        if not (lo <= month <= hi):
            raise ApiError(422, "MONTH_OUT_OF_RANGE", "Month Out Of Range", f"{lo:%Y-%m} ~ {hi:%Y-%m} 사이만 가능")
        prev_m, prev_y = _add(month, -1), _add(month, -12)
        spark_from = _add(month, -11)

        sgg_rows = await _q(
            request,
            """
            SELECT sgg_cd, month, trades, cancelled, priced, median_ppm2, low_sample
            FROM region_month WHERE month IN ({m:Date}, {pm:Date}, {py:Date})""",
            {"m": month, "pm": prev_m, "py": prev_y},
        )
        roll = await _q(
            request,
            """
            SELECT region_id, month, trades, cancelled, priced, p25_ppm2, median_ppm2, p75_ppm2, low_sample
            FROM rollup_month WHERE month BETWEEN {a:Date} AND {b:Date} OR month = {py:Date}
            ORDER BY region_id, month""",
            {"a": spark_from, "b": month, "py": prev_y},
        )

        by = {(r["sgg_cd"], r["month"]): r for r in sgg_rows}
        sgg_out = []
        for (code, m), r in by.items():
            if m != month:
                continue
            pm, py = by.get((code, prev_m)), by.get((code, prev_y))
            reported = r["trades"] + r["cancelled"]
            ok_change = (
                py is not None and r["priced"] >= MIN_SAMPLE_FOR_CHANGE and py["priced"] >= MIN_SAMPLE_FOR_CHANGE
            )
            sgg_out.append(
                {
                    "sggCd": code,
                    "trades": r["trades"],
                    "cancelled": r["cancelled"],
                    "cancelRate": round(r["cancelled"] / reported * 100, 2) if reported else None,
                    "median": _r(r["median_ppm2"], 1),
                    "sample": r["priced"],
                    "lowSample": bool(r["low_sample"]),
                    "medianYoY": _pct(r["median_ppm2"], py["median_ppm2"]) if ok_change and py else None,
                    "tradesYoY": _pct(r["trades"], py["trades"]) if py else None,
                    "tradesMoM": _pct(r["trades"], pm["trades"]) if pm else None,
                }
            )

        series: dict[str, list[dict]] = {}
        for r in roll:
            series.setdefault(r["region_id"], []).append(r)

        def region_summary(rid: str) -> dict | None:
            rows = series.get(rid, [])
            cur = next((x for x in rows if x["month"] == month), None)
            if cur is None:
                return None
            pm = next((x for x in rows if x["month"] == prev_m), None)
            py = next((x for x in rows if x["month"] == prev_y), None)
            reported = cur["trades"] + cur["cancelled"]
            spark = [x for x in rows if spark_from <= x["month"] <= month]
            return {
                "regionId": rid,
                "trades": cur["trades"],
                "cancelled": cur["cancelled"],
                "cancelRate": round(cur["cancelled"] / reported * 100, 2) if reported else None,
                "median": _r(cur["median_ppm2"], 1),
                "p25": _r(cur["p25_ppm2"], 1),
                "p75": _r(cur["p75_ppm2"], 1),
                "sample": cur["priced"],
                "tradesMoM": _pct(cur["trades"], pm["trades"]) if pm else None,
                "tradesYoY": _pct(cur["trades"], py["trades"]) if py else None,
                "medianYoY": _pct(cur["median_ppm2"], py["median_ppm2"]) if py else None,
                "spark": {
                    "months": [f"{x['month']:%Y-%m}" for x in spark],
                    "trades": [x["trades"] for x in spark],
                    "median": [_r(x["median_ppm2"], 1) for x in spark],
                },
            }

        sido = [s for s in (region_summary(rid) for rid in sorted(series) if rid != "00") if s]
        eligible = [s for s in sgg_out if s["medianYoY"] is not None]
        return {
            "month": f"{month:%Y-%m}",
            "provisional": provisional(month),
            "available": {"from": f"{lo:%Y-%m}", "to": f"{hi:%Y-%m}", "default": f"{_default_month(hi):%Y-%m}"},
            "nation": region_summary("00"),
            "sido": sido,
            "sgg": sgg_out,
            "rankings": {
                "volume": sorted(sgg_out, key=lambda s: s["trades"], reverse=True)[:10],
                "gainers": sorted(eligible, key=lambda s: s["medianYoY"], reverse=True)[:10],
                "losers": sorted(eligible, key=lambda s: s["medianYoY"])[:10],
            },
            "definitions": {
                "median": "㎡당 거래가 중위수(만원/㎡) — 해제·이상치 제외",
                "medianYoY": f"전년 같은 달 대비 중위수 변화율(%) — 두 달 모두 표본 {MIN_SAMPLE_FOR_CHANGE}건 이상일 때만",
                "cancelRate": "해제 건수 ÷ 신고 건수(%)",
            },
            "disclaimer": DISCLAIMER,
        }, 0

    return await respond(request, "market_overview", {"ym": ym}, compute)


@router.get("/market/ticker", summary="상단 지표 띠: 전국 거래·중위가, 자체 지수 월간 변화")
async def ticker(request: Request, p: Principal = require_scope("read")) -> Response:
    async def compute():
        lo, hi = await _month_bounds(request)
        if lo is None or hi is None:
            return {"items": [], "available": None}, 0
        m = _default_month(hi)
        roll = await _q(
            request,
            """SELECT month, trades, cancelled, median_ppm2 FROM rollup_month
                                    WHERE region_id = '00' AND month IN ({m:Date}, {pm:Date}, {py:Date})""",
            {"m": m, "pm": _add(m, -1), "py": _add(m, -12)},
        )
        by = {r["month"]: r for r in roll}
        items = []
        cur, pm, py = by.get(m), by.get(_add(m, -1)), by.get(_add(m, -12))
        if cur:
            items.append(
                {
                    "key": "trades",
                    "label": f"전국 거래 {m:%Y-%m}",
                    "value": cur["trades"],
                    "unit": "건",
                    "change": _pct(cur["trades"], pm["trades"]) if pm else None,
                    "changeBasis": "전월 대비",
                }
            )
            items.append(
                {
                    "key": "median",
                    "label": "전국 ㎡당 중위가",
                    "value": _r(cur["median_ppm2"], 0),
                    "unit": "만원/㎡",
                    "change": _pct(cur["median_ppm2"], py["median_ppm2"]) if py else None,
                    "changeBasis": "전년 동월 대비",
                }
            )
            rep = cur["trades"] + cur["cancelled"]
            items.append(
                {
                    "key": "cancel",
                    "label": "해제율",
                    "value": round(cur["cancelled"] / rep * 100, 1) if rep else None,
                    "unit": "%",
                    "change": None,
                    "changeBasis": None,
                }
            )
        idx = await _q(
            request,
            """
            SELECT region_id, period, index_value FROM price_index
            WHERE method = 'HEDONIC_TD_v1' AND region_id IN ('00','11','41','26')
            ORDER BY region_id, period DESC LIMIT 8 BY region_id""",
            {},
        )
        names = {"00": "전국", "11": "서울", "41": "경기", "26": "부산"}
        by_r: dict[str, list] = {}
        for r in sorted(idx, key=lambda x: x["period"]):
            by_r.setdefault(r["region_id"], []).append(r)
        for rid in ["00", "11", "41", "26"]:
            # 확정된 달끼리만 비교 (잠정 달은 신고가 더 들어오며 값이 바뀐다)
            rows = [r for r in by_r.get(rid, []) if not provisional(r["period"])]
            if len(rows) >= 2 and rows[-1]["period"] == _add(rows[-2]["period"], 1):
                items.append(
                    {
                        "key": f"index_{rid}",
                        "label": f"{names[rid]} 지수 {rows[-1]['period']:%Y-%m}",
                        "value": round(rows[-1]["index_value"], 1),
                        "unit": "",
                        "change": _pct(rows[-1]["index_value"], rows[-2]["index_value"]),
                        "changeBasis": "전월 대비 (확정 월)",
                        "provisional": False,
                    }
                )
        return {
            "month": f"{m:%Y-%m}",
            "items": items,
            "available": {"from": f"{lo:%Y-%m}", "to": f"{hi:%Y-%m}", "default": f"{m:%Y-%m}"},
        }, 0

    return await respond(request, "market_ticker", {}, compute)


# ───────────────────────── 지도 경계 ─────────────────────────


@router.get("/geo/sgg", summary="시군구 경계 GeoJSON (시각화용 단순화본, V-World)")
async def geo_sgg(request: Request, p: Principal = require_scope("read")) -> Response:
    cache = request.app.state.geo_cache
    async with request.app.state.res.pg.connection() as c:
        meta = await (
            await c.execute(
                """SELECT max(source_sha256) AS sha, max(fetched_at) AS at, count(*) AS n, max(source) AS src,
                      percentile_cont(0.5) WITHIN GROUP (ORDER BY area_rel_error) AS err_med,
                      max(area_rel_error) AS err_max
               FROM ops.region_boundary"""
            )
        ).fetchone()
        if not meta or not meta["n"]:
            raise ApiError(404, "BOUNDARY_NOT_READY", "Not Found", "경계 데이터가 아직 없습니다 (make lake-init).")
        etag = f'"geo-{meta["sha"][:16]}-{meta["n"]}"'
        headers = {**p.limit_headers, "ETag": etag, "Cache-Control": "public, max-age=86400"}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        if cache.get("etag") != etag:
            rows = await (
                await c.execute("SELECT sgg_cd, geometry FROM ops.region_boundary ORDER BY sgg_cd")
            ).fetchall()
            fc = {
                "type": "FeatureCollection",
                "source": f"{meta['src']} (국토정보플랫폼), 시각화용 단순화 — 면적 오차 중앙값 "
                f"{meta['err_med'] * 100:.2f}%, 최대 {meta['err_max'] * 100:.2f}%",
                "fetchedAt": meta["at"].isoformat(),
                "features": [
                    {
                        "type": "Feature",
                        "id": r["sgg_cd"],
                        "properties": {"sggCd": r["sgg_cd"]},
                        "geometry": r["geometry"] if isinstance(r["geometry"], dict) else json.loads(r["geometry"]),
                    }
                    for r in rows
                ],
            }
            body = orjson.dumps(fc)
            # 1.1MB 를 요청마다 압축하면 약 200ms(측정) → 데이터 버전당 한 번만 압축해 둔다
            cache.update(etag=etag, body=body, gz=gzip.compress(body, compresslevel=9, mtime=0))
    headers["Vary"] = "Accept-Encoding"
    if "gzip" in request.headers.get("accept-encoding", ""):
        # Content-Encoding 이 이미 있으면 GZip 미들웨어는 다시 압축하지 않고 그대로 보낸다
        return Response(cache["gz"], media_type="application/geo+json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(cache["body"], media_type="application/geo+json", headers=headers)


# ───────────────────────── 검색 ─────────────────────────
