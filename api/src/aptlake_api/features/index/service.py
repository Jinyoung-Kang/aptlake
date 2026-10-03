"""자체 지수(HEDONIC_TD_v1): 지역 시계열 + R-ONE 대비 검증, 지역별 요약 — 업무 규칙 (순수)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any, Protocol

from ...core.problems import ApiError
from ...core.texts import DISCLAIMER
from ...core.values import add_months, pct_change, rnd

Row = dict[str, Any]
METHOD = "HEDONIC_TD_v1"


class IndexStore(Protocol):
    async def series(self, region: str, method: str) -> list[Row]: ...
    async def reference(self, region: str) -> list[Row]: ...
    async def validation(self, region: str, method: str) -> Row | None: ...
    async def all_series(self, method: str) -> list[Row]: ...
    async def all_validations(self, method: str) -> list[Row]: ...


async def region_index(
    store: IndexStore, region: str, method: str, *, is_provisional: Callable[[dt.date], bool]
) -> tuple[dict, int]:
    series = await store.series(region, method)
    ref = await store.reference(region)
    v = await store.validation(region, method)
    if not series:
        raise ApiError(404, "INDEX_NOT_FOUND", "Not Found", f"{region} 지역 지수가 아직 없습니다.")
    return {
        "regionId": region,
        "method": method,
        "base": f"{series[0]['period']:%Y-%m}=100",
        "series": [
            {
                "period": f"{r['period']:%Y-%m}",
                "value": round(r["index_value"], 2),
                "ciLow": round(r["ci_low"], 2),
                "ciHigh": round(r["ci_high"], 2),
                "nObs": r["n_obs"],
                **({"provisional": True} if is_provisional(r["period"]) else {}),
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


def index_point(by: dict[dt.date, float], m: dt.date, *, is_provisional: Callable[[dt.date], bool]) -> dict[str, Any]:
    return {
        "period": f"{m:%Y-%m}",
        "value": round(by[m], 2),
        "provisional": is_provisional(m),
        "mom": pct_change(by[m], by.get(add_months(m, -1))),
        "yoy": pct_change(by[m], by.get(add_months(m, -12))),
    }


async def summary(store: IndexStore, *, is_provisional: Callable[[dt.date], bool]) -> tuple[dict, int]:
    val = {r["region_id"]: r for r in await store.all_validations(METHOD)}
    out = []
    for r in await store.all_series(METHOD):
        periods, vals = [x[0] for x in r["pts"]], [x[1] for x in r["pts"]]
        by = dict(zip(periods, vals, strict=True))
        # 대표값은 확정된 최근 달 — 잠정 달은 신고가 더 들어오며 바뀐다 (차트·표에서 따로 표시)
        done = [m for m in periods if not is_provisional(m)]
        v = val.get(r["region_id"])
        out.append(
            {
                "regionId": r["region_id"],
                **index_point(by, periods[-1], is_provisional=is_provisional),
                "confirmed": index_point(by, done[-1], is_provisional=is_provisional) if done else None,
                "spark": [round(x, 2) for x in vals[-24:]],
                "corrMoM": rnd(v["corr_mom"], 3) if v else None,
                "directionMatch": rnd(v["direction_match"], 3) if v else None,
                "months": v["n_months"] if v else 0,
            }
        )
    return {"items": sorted(out, key=lambda x: x["regionId"])}, 0
