"""지역: 시군구 목록, 월별 통계, 한 달 가격 분포, 기간 내 단지 순위 — 업무 규칙.

순수 계층: FastAPI·DB 드라이버를 모른다. 데이터는 RegionStore(저장소 Protocol)로만 받는다.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.texts import DISCLAIMER
from ...core.values import rnd

Row = dict[str, Any]
AREA_BANDS = [(None, 40), (40, 60), (60, 85), (85, 135), (135, None)]  # 지수 모형(HEDONIC_TD_v1)과 같은 경계
FLOOR_BANDS = [(None, 3), (4, 10), (11, 20), (21, None)]
SCATTER_LIMIT = 3000  # 산점도 점 상한 (한 달·한 시군구)


class RegionStore(Protocol):
    async def all(self) -> list[Row]: ...
    async def months(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def price_stats(self, sgg: str, a: dt.date, b: dt.date) -> Row: ...
    async def histogram(self, sgg: str, a: dt.date, b: dt.date, width: float) -> list[Row]: ...
    async def by_area(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def by_floor(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def points(self, sgg: str, a: dt.date, b: dt.date, limit: int) -> list[Row]: ...
    async def top_complexes(self, sgg: str, a: dt.date, b: dt.date, limit: int) -> list[Row]: ...


async def regions(store: RegionStore) -> tuple[dict, int]:
    items = [
        {
            "sggCd": r["sgg_cd"],
            "sidoCd": r["sido_cd"],
            "sidoName": r["sido_nm"],
            "name": r["sgg_nm"],
            "fullName": r["full_nm"],
        }
        for r in await store.all()
    ]
    # 참조 메타데이터는 일일 '데이터 행' 한도에 넣지 않는다
    return {"items": items, "source": "행정안전부 법정동코드 (StanReginCd)"}, 0


async def region_months(
    store: RegionStore,
    names: dict[str, Row],
    sgg: str,
    start: dt.date,
    end: dt.date,
    *,
    is_provisional: Callable[[dt.date], bool],
    provisional_days: int,
) -> tuple[dict, int]:
    reg = names.get(sgg)
    if reg is None:
        raise ApiError(400, "INVALID_REGION", "Invalid Region", f"알 수 없는 시군구 코드 {sgg}")
    items = []
    for r in await store.months(sgg, start, end):
        item = {
            "dealYm": r["month"].strftime("%Y-%m"),
            "reported": r["reported"],
            "trades": r["trades"],
            "cancelled": r["cancelled"],
            "sampleSize": r["priced"],
            "outliers": r["outliers"],
            "p25PricePerM2": rnd(r["p25_ppm2"]),
            "medianPricePerM2": rnd(r["median_ppm2"]),
            "p75PricePerM2": rnd(r["p75_ppm2"]),
            "unit": "만원/㎡",
        }
        if r["low_sample"]:
            item["lowSample"] = True
        if is_provisional(r["month"]):
            item["provisional"] = True
        items.append(item)
    return {
        "region": {"sggCd": sgg, "name": reg["sgg_nm"], "fullName": reg["full_nm"]},
        "items": items,
        "notes": [
            "거래 건수는 해제 거래를 제외한 현재 신고 건수, 분위수는 해제·면적 0·월 전국 상하위 0.1% 제외.",
            "표본 5건 미만이면 분위수는 null, lowSample=true.",
            f"계약월 말일 + {provisional_days}일 전까지는 신고가 추가될 수 있어 provisional=true.",
        ],
        "disclaimer": DISCLAIMER,
    }, 0  # 집계 응답: 요청 한도만 (행 한도는 거래 단위 레코드에만)


def histogram_width(p05: float | None, p95: float | None) -> float:
    """5~95백분위 범위를 약 16칸으로, 칸 너비는 보기 좋은 수(10·20·25·50·100…)."""
    span = max((p95 or 0) - (p05 or 0), 1.0)
    raw = span / 16
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


async def distribution(
    store: RegionStore, sgg: str, ym: str, month: dt.date, month_end: dt.date, *, provisional: bool
) -> tuple[dict, int]:
    stats = await store.price_stats(sgg, month, month_end)
    if not stats["n"]:
        return {"month": ym, "stats": None, "histogram": [], "byArea": [], "byFloor": [], "points": []}, 0
    width = histogram_width(stats["p05"], stats["p95"])
    hist = await store.histogram(sgg, month, month_end, width)
    by_area = await store.by_area(sgg, month, month_end)
    by_floor = await store.by_floor(sgg, month, month_end)
    points = await store.points(sgg, month, month_end, SCATTER_LIMIT)
    return {
        "month": ym,
        "provisional": provisional,
        "stats": {
            "reported": stats["n"],
            "cancelled": stats["cancelled"],
            "sample": stats["priced"],
            "p05": rnd(stats["p05"], 1),
            "p25": rnd(stats["p25"], 1),
            "median": rnd(stats["p50"], 1),
            "p75": rnd(stats["p75"], 1),
            "p95": rnd(stats["p95"], 1),
        },
        "histogram": [{"lo": round(h["lo"], 1), "hi": round(h["lo"] + width, 1), "n": h["n"]} for h in hist],
        "binWidth": width,
        "byArea": [
            {
                "band": r["band"],
                "n": r["n"],
                "medianPpm2": rnd(r["median_ppm2"], 1),
                "medianPrice": rnd(r["median_price"], 0),
            }
            for r in by_area
        ],
        "byFloor": [{"band": r["band"], "n": r["n"], "medianPpm2": rnd(r["median_ppm2"], 1)} for r in by_floor],
        "points": [[round(x["area"], 2), x["price"], x["floor"], x["c"], x["o"]] for x in points],
        "pointFields": ["areaM2", "priceManwon", "floor", "cancelled", "outlier"],
        "notes": [
            "히스토그램·면적대·층별은 해제·이상치 제외, 산점도는 전체(해제·이상치 표시).",
            "면적대 경계는 자체 지수 모형과 같은 40·60·85·135㎡.",
        ],
    }, len(points)  # 산점도 점은 거래 단위 레코드 → 행 한도에 넣는다


def check_period(a: dt.date, b: dt.date) -> None:
    if b < a:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range")


async def top_complexes(
    store: RegionStore, sgg: str, from_: str, to: str, a: dt.date, b: dt.date, limit: int
) -> tuple[dict, int]:
    rows = await store.top_complexes(sgg, a, b, limit)
    return {
        "from": from_,
        "to": to,
        "items": [
            {
                "complexKey": r["complex_key"],
                "aptName": r["apt"],
                "umdName": r["umd"],
                "buildYear": r["built"],
                "trades": r["n"],
                "cancelled": r["cancelled"],
                "medianPpm2": rnd(r["med"], 1),
                "lastDate": r["last_date"].isoformat(),
                "lastPrice": r["last_price"],
                "lastArea": round(r["last_area"], 2),
            }
            for r in rows
        ],
    }, 0
