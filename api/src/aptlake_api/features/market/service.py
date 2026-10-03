"""시장 개요·시세 띠·경계 — 업무 규칙 (순수).

집계 응답(시군구·시도 통계, 순위)은 원자료를 내보내지 않으므로 일일 '데이터 행' 한도에 넣지 않는다.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.texts import DISCLAIMER
from ...core.values import add_months, pct_change, rnd

Row = dict[str, Any]
MIN_SAMPLE_FOR_CHANGE = 30  # 변화율 순위: 두 달 모두 표본 30건 이상인 시군구만 (작은 표본의 착시 방지)
TICKER_INDEX = {"00": "전국", "11": "서울", "41": "경기", "26": "부산"}
INDEX_METHOD = "HEDONIC_TD_v1"


class MarketStore(Protocol):
    async def month_bounds(self) -> tuple[dt.date | None, dt.date | None]: ...
    async def sgg_months(self, m: dt.date, pm: dt.date, py: dt.date) -> list[Row]: ...
    async def rollups(self, a: dt.date, b: dt.date, py: dt.date) -> list[Row]: ...
    async def nation_months(self, m: dt.date, pm: dt.date, py: dt.date) -> list[Row]: ...
    async def index_tail(self, method: str, regions: list[str], per_region: int) -> list[Row]: ...


def default_month(latest: dt.date, is_provisional: Callable[[dt.date], bool]) -> dt.date:
    """기본 기준월 = 잠정이 아닌 가장 최근 달 (최근 달은 신고가 계속 들어와 비교가 왜곡된다)."""
    m = latest
    for _ in range(6):
        if not is_provisional(m):
            return m
        m = add_months(m, -1)
    return latest


def _cancel_rate(cancelled: int, reported: int) -> float | None:
    return round(cancelled / reported * 100, 2) if reported else None


def sgg_summaries(rows: list[Row], month: dt.date) -> list[dict[str, Any]]:
    prev_m, prev_y = add_months(month, -1), add_months(month, -12)
    by = {(r["sgg_cd"], r["month"]): r for r in rows}
    out = []
    for (code, m), r in by.items():
        if m != month:
            continue
        pm, py = by.get((code, prev_m)), by.get((code, prev_y))
        ok_change = py is not None and r["priced"] >= MIN_SAMPLE_FOR_CHANGE and py["priced"] >= MIN_SAMPLE_FOR_CHANGE
        out.append(
            {
                "sggCd": code,
                "trades": r["trades"],
                "cancelled": r["cancelled"],
                "cancelRate": _cancel_rate(r["cancelled"], r["trades"] + r["cancelled"]),
                "median": rnd(r["median_ppm2"], 1),
                "sample": r["priced"],
                "lowSample": bool(r["low_sample"]),
                "medianYoY": pct_change(r["median_ppm2"], py["median_ppm2"]) if ok_change and py else None,
                "tradesYoY": pct_change(r["trades"], py["trades"]) if py else None,
                "tradesMoM": pct_change(r["trades"], pm["trades"]) if pm else None,
            }
        )
    return out


def region_summary(rows: list[Row], month: dt.date) -> dict[str, Any] | None:
    """시도·전국 한 지역의 기준월 요약 + 최근 12개월 추이."""
    prev_m, prev_y, spark_from = add_months(month, -1), add_months(month, -12), add_months(month, -11)
    cur = next((x for x in rows if x["month"] == month), None)
    if cur is None:
        return None
    pm = next((x for x in rows if x["month"] == prev_m), None)
    py = next((x for x in rows if x["month"] == prev_y), None)
    spark = [x for x in rows if spark_from <= x["month"] <= month]
    return {
        "regionId": cur["region_id"],
        "trades": cur["trades"],
        "cancelled": cur["cancelled"],
        "cancelRate": _cancel_rate(cur["cancelled"], cur["trades"] + cur["cancelled"]),
        "median": rnd(cur["median_ppm2"], 1),
        "p25": rnd(cur["p25_ppm2"], 1),
        "p75": rnd(cur["p75_ppm2"], 1),
        "sample": cur["priced"],
        "tradesMoM": pct_change(cur["trades"], pm["trades"]) if pm else None,
        "tradesYoY": pct_change(cur["trades"], py["trades"]) if py else None,
        "medianYoY": pct_change(cur["median_ppm2"], py["median_ppm2"]) if py else None,
        "spark": {
            "months": [f"{x['month']:%Y-%m}" for x in spark],
            "trades": [x["trades"] for x in spark],
            "median": [rnd(x["median_ppm2"], 1) for x in spark],
        },
    }


async def overview(
    store: MarketStore, ym: dt.date | None, *, is_provisional: Callable[[dt.date], bool]
) -> tuple[dict, int]:
    lo, hi = await store.month_bounds()
    if lo is None or hi is None:
        raise ApiError(404, "NO_DATA", "Not Found", "아직 발행된 데이터가 없습니다.")
    month = ym or default_month(hi, is_provisional)
    if not (lo <= month <= hi):
        raise ApiError(422, "MONTH_OUT_OF_RANGE", "Month Out Of Range", f"{lo:%Y-%m} ~ {hi:%Y-%m} 사이만 가능")
    prev_m, prev_y = add_months(month, -1), add_months(month, -12)
    sgg_out = sgg_summaries(await store.sgg_months(month, prev_m, prev_y), month)
    series: dict[str, list[Row]] = {}
    for r in await store.rollups(add_months(month, -11), month, prev_y):
        series.setdefault(r["region_id"], []).append(r)
    sido = [s for s in (region_summary(series[rid], month) for rid in sorted(series) if rid != "00") if s]
    eligible = [s for s in sgg_out if s["medianYoY"] is not None]
    return {
        "month": f"{month:%Y-%m}",
        "provisional": is_provisional(month),
        "available": {
            "from": f"{lo:%Y-%m}",
            "to": f"{hi:%Y-%m}",
            "default": f"{default_month(hi, is_provisional):%Y-%m}",
        },
        "nation": region_summary(series.get("00", []), month),
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


async def ticker(store: MarketStore, *, is_provisional: Callable[[dt.date], bool]) -> tuple[dict, int]:
    lo, hi = await store.month_bounds()
    if lo is None or hi is None:
        return {"items": [], "available": None}, 0
    m = default_month(hi, is_provisional)
    by = {r["month"]: r for r in await store.nation_months(m, add_months(m, -1), add_months(m, -12))}
    items: list[dict[str, Any]] = []
    cur, pm, py = by.get(m), by.get(add_months(m, -1)), by.get(add_months(m, -12))
    if cur:
        items.append(
            {
                "key": "trades",
                "label": f"전국 거래 {m:%Y-%m}",
                "value": cur["trades"],
                "unit": "건",
                "change": pct_change(cur["trades"], pm["trades"]) if pm else None,
                "changeBasis": "전월 대비",
            }
        )
        items.append(
            {
                "key": "median",
                "label": "전국 ㎡당 중위가",
                "value": rnd(cur["median_ppm2"], 0),
                "unit": "만원/㎡",
                "change": pct_change(cur["median_ppm2"], py["median_ppm2"]) if py else None,
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
    by_r: dict[str, list[Row]] = {}
    for r in sorted(await store.index_tail(INDEX_METHOD, list(TICKER_INDEX), 8), key=lambda x: x["period"]):
        by_r.setdefault(r["region_id"], []).append(r)
    for rid, name in TICKER_INDEX.items():
        # 확정된 달끼리만 비교 (잠정 달은 신고가 더 들어오며 값이 바뀐다)
        rows = [r for r in by_r.get(rid, []) if not is_provisional(r["period"])]
        if len(rows) >= 2 and rows[-1]["period"] == add_months(rows[-2]["period"], 1):
            items.append(
                {
                    "key": f"index_{rid}",
                    "label": f"{name} 지수 {rows[-1]['period']:%Y-%m}",
                    "value": round(rows[-1]["index_value"], 1),
                    "unit": "",
                    "change": pct_change(rows[-1]["index_value"], rows[-2]["index_value"]),
                    "changeBasis": "전월 대비 (확정 월)",
                    "provisional": False,
                }
            )
    return {
        "month": f"{m:%Y-%m}",
        "items": items,
        "available": {"from": f"{lo:%Y-%m}", "to": f"{hi:%Y-%m}", "default": f"{m:%Y-%m}"},
    }, 0


def boundary_etag(meta: Row) -> str:
    return f'"geo-{meta["sha"][:16]}-{meta["n"]}"'


def feature_collection(meta: Row, shapes: list[Row]) -> dict[str, Any]:
    return {
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
            for r in shapes
        ],
    }
