"""전월세: 지역 월별 통계·전세가율·가격 분포·계약 목록·계약 이력·단지 요약 — 업무 규칙 (순수, ADR-050).

정의 (화면·응답 notes 에 그대로 밝힌다):
  - 전세 = 월세가 0 인 계약. 원천에 전세·월세 구분 항목이 없고 월세는 빈 값 없이 늘 숫자로 온다.
  - 계약 구분·갱신요구권은 원천 낱말 그대로('신규'·'갱신', '사용'). 빈 값은 '미상'이고 신규로 세지 않는다.
  - 전세가율(지역) = 같은 시군구, 그 달까지 3개월 창의 전세 보증금 ㎡당 중앙값 ÷ 매매 ㎡당 중앙값.
    양쪽 표본(해제·이상치·면적 0 제외)이 모두 5건 이상일 때만. 자체 산출 값이다.
  - 전세가율(단지) = 같은 단지·같은 전용면적 구간(60·85·135㎡), 최근 12개월, 양쪽 표본 3건 이상.
    '최근'은 발행된 전월세 자료의 마지막 계약월까지 — 오늘 날짜가 아니라 자료 기준이라 같은 버전이면 응답이 같다.
  두 비율 모두 서빙 시점에 발행된 두 표(trade_current·rent_current)에서 정확 분위수로 계산한다 — gold 에 미리 두면
  두 원천의 발행 시점이 달라 한쪽 발행 뒤 낡은 비율이 남는다.
순수 계층: FastAPI·DB 드라이버를 모른다. 데이터는 RentStore(Protocol)로만 받는다.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ...core import cursor
from ...core.problems import ApiError
from ...core.texts import DISCLAIMER
from ...core.values import add_months, rnd, ts

Row = dict[str, Any]
RENT_ID = re.compile(r"^r(\d{5})-(\d{6})-[0-9a-f]{16}-\d{1,4}$")
RATIO_WINDOW_MONTHS = 3
RATIO_MIN_SAMPLE = 5
COMPLEX_WINDOW_MONTHS = 12
COMPLEX_MIN_SAMPLE = 3
COMPLEX_RECENT = 10
# 단지 화면의 전용면적 구간과 같다 (web domain/complex.ts COMPLEX_BANDS)
COMPLEX_BANDS: list[tuple[str, float, float]] = [
    ("60m² 미만", 0, 60),
    ("60~85m²", 60, 85),
    ("85~135m²", 85, 135),
    ("135m² 이상", 135, math.inf),
]
KINDS = ("jeonse", "wolse")
DEFINITIONS = {
    "jeonse": "전세 = 월세가 0 인 계약 (원천에 전세·월세 구분 항목이 없어 정의로 나눈다)",
    "contractType": "계약 구분은 원천 낱말 그대로 — '신규'·'갱신', 빈 값은 미상(신규로 세지 않음)",
    "useRRRight": "갱신요구권 사용은 원천 낱말 '사용' 인 계약 수, 빈 값은 미상",
}
RATIO_NOTE = (
    f"전세가율은 자체 산출 값 — 같은 시군구, 그 달까지 {RATIO_WINDOW_MONTHS}개월 창의 전세 보증금 ㎡당 중앙값 ÷ "
    f"매매 ㎡당 중앙값. 양쪽 표본(해제·이상치·면적 0 제외)이 모두 {RATIO_MIN_SAMPLE}건 이상일 때만."
)
COMPLEX_RATIO_NOTE = (
    f"단지 전세가율은 자체 산출 값 — 같은 전용면적 구간, 최근 {COMPLEX_WINDOW_MONTHS}개월의 전세 보증금 ㎡당 중앙값 ÷ "
    f"매매 ㎡당 중앙값. 양쪽 표본이 모두 {COMPLEX_MIN_SAMPLE}건 이상일 때만."
)


class RentStore(Protocol):
    async def months(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def window_medians(self, sgg: str, a: dt.date, b: dt.date, months: int) -> tuple[list[Row], list[Row]]: ...
    async def price_stats(self, sgg: str, a: dt.date, b: dt.date) -> Row: ...
    async def histogram(self, sgg: str, a: dt.date, b: dt.date, width: float) -> list[Row]: ...
    async def by_area(self, sgg: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def page(self, f: RentFilter, after: tuple[dt.date, str] | None, limit: int) -> list[Row]: ...
    async def summary(self, f: RentFilter) -> Row: ...
    async def versions(self, rent_id: str, yyyymm: int) -> list[Row]: ...
    async def complex_rents(self, sgg: str, key: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def complex_trades(self, sgg: str, key: str, a: dt.date, b: dt.date) -> list[Row]: ...
    async def complex_recent(self, sgg: str, key: str, limit: int) -> list[Row]: ...
    async def latest_month(self) -> dt.date | None: ...


# ───────────────────────── 지역 월별 ─────────────────────────


def _month_item(r: Row, is_provisional: Callable[[dt.date], bool]) -> dict[str, Any]:
    item = {
        "dealYm": r["month"].strftime("%Y-%m"),
        "contracts": r["contracts"],
        "jeonse": r["jeonse"],
        "wolse": r["wolse"],
        "renewals": r["renewals"],
        "newContracts": r["new_contracts"],
        "typeUnknown": r["contracts"] - r["renewals"] - r["new_contracts"],
        "renewalRightUsed": r["rr_used"],
        "jeonseSample": r["jeonse_priced"],
        "outliers": r["outliers"],
        "jeonseP25PerM2": rnd(r["jeonse_p25_ppm2"]),
        "jeonseMedianPerM2": rnd(r["jeonse_median_ppm2"]),
        "jeonseP75PerM2": rnd(r["jeonse_p75_ppm2"]),
        "wolseMedianRent": rnd(r["wolse_median_rent"]),
        "wolseMedianDeposit": rnd(r["wolse_median_deposit"], 0),
    }
    if r["low_sample"]:
        item["lowSample"] = True
    if is_provisional(r["month"]):
        item["provisional"] = True
    return item


async def rent_months(
    store: RentStore,
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
    return {
        "region": {"sggCd": sgg, "name": reg["sgg_nm"], "fullName": reg["full_nm"]},
        "items": [_month_item(r, is_provisional) for r in await store.months(sgg, start, end)],
        "units": {"perM2": "만원/㎡", "rent": "만원/월", "deposit": "만원"},
        "definitions": DEFINITIONS,
        "notes": [
            "계약 수는 현재 신고 기준(원천에서 사라진 계약 포함 — missingSince), 전세 분위수는 면적 0·월 전국 상하위 0.1% 제외.",
            "전세 표본 5건 미만이면 분위수는 null, lowSample=true. 월세 중앙값도 월세 계약 5건 미만이면 null.",
            "2021-06 신고 의무화 전 계약은 계약 구분이 대부분 비어 있다(미상).",
            f"계약월 말일 + {provisional_days}일 전까지는 신고가 추가될 수 있어 provisional=true.",
        ],
        "disclaimer": DISCLAIMER,
    }, 0  # 집계 응답: 행 한도에 넣지 않는다 (ADR-024)


# ───────────────────────── 전세가율 ─────────────────────────


def ratio_pct(jeonse_med: float | None, trade_med: float | None, nj: int, nt: int, min_sample: int) -> float | None:
    """전세 보증금 ㎡당 중앙값 ÷ 매매 ㎡당 중앙값 (%, 소수 첫째 자리). 양쪽 표본이 min_sample 이상일 때만."""
    if nj < min_sample or nt < min_sample or not jeonse_med or not trade_med:
        return None
    return round(jeonse_med / trade_med * 100, 1)


async def jeonse_ratio(
    store: RentStore,
    names: dict[str, Row],
    sgg: str,
    start: dt.date,
    end: dt.date,
    *,
    is_provisional: Callable[[dt.date], bool],
) -> tuple[dict, int]:
    reg = names.get(sgg)
    if reg is None:
        raise ApiError(400, "INVALID_REGION", "Invalid Region", f"알 수 없는 시군구 코드 {sgg}")
    trade, rent = await store.window_medians(sgg, start, end, RATIO_WINDOW_MONTHS)
    t_by = {r["m"]: r for r in trade}
    r_by = {r["m"]: r for r in rent}
    items = []
    m = start
    while m <= end:
        t, j = t_by.get(m), r_by.get(m)
        nt, nj = (t["n"] if t else 0), (j["n"] if j else 0)
        item: dict[str, Any] = {
            "dealYm": m.strftime("%Y-%m"),
            "windowFrom": add_months(m, -(RATIO_WINDOW_MONTHS - 1)).strftime("%Y-%m"),
            "tradeMedianPerM2": rnd(t["med"]) if t else None,
            "tradeSample": nt,
            "jeonseMedianPerM2": rnd(j["med"]) if j else None,
            "jeonseSample": nj,
            "ratioPct": ratio_pct(j["med"] if j else None, t["med"] if t else None, nj, nt, RATIO_MIN_SAMPLE),
        }
        if is_provisional(m):
            item["provisional"] = True
        items.append(item)
        m = add_months(m, 1)
    return {
        "region": {"sggCd": sgg, "name": reg["sgg_nm"], "fullName": reg["full_nm"]},
        "items": items,
        "definition": {"windowMonths": RATIO_WINDOW_MONTHS, "minSample": RATIO_MIN_SAMPLE, "unit": "%"},
        "notes": [RATIO_NOTE, DEFINITIONS["jeonse"], "잠정 달이 창에 들어가면 그 달 비율도 잠정(provisional)."],
        "disclaimer": DISCLAIMER,
    }, 0


# ───────────────────────── 가격 분포 ─────────────────────────


def histogram_width(p05: float | None, p95: float | None) -> float:
    """5~95백분위 범위를 약 16칸으로, 칸 너비는 보기 좋은 수 (지역 매매 분포와 같은 규칙)."""
    span = max((p95 or 0) - (p05 or 0), 1.0)
    raw = span / 16
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


async def distribution(
    store: RentStore, sgg: str, ym: str, month: dt.date, month_end: dt.date, *, provisional: bool
) -> tuple[dict, int]:
    s = await store.price_stats(sgg, month, month_end)
    if not s["n"]:
        return {"month": ym, "stats": None, "histogram": [], "byArea": []}, 0
    width = histogram_width(s["p05"], s["p95"]) if s["priced"] else 1.0
    hist = await store.histogram(sgg, month, month_end, width) if s["priced"] else []
    return {
        "month": ym,
        "provisional": provisional,
        "stats": {
            "contracts": s["n"],
            "jeonse": s["jeonse"],
            "wolse": s["n"] - s["jeonse"],
            "jeonseSample": s["priced"],
            "p05": rnd(s["p05"], 1),
            "p25": rnd(s["p25"], 1),
            "median": rnd(s["p50"], 1),
            "p75": rnd(s["p75"], 1),
            "p95": rnd(s["p95"], 1),
            "wolseMedianRent": rnd(s["rent_med"], 1),
            "wolseMedianDeposit": rnd(s["dep_med"], 0),
        },
        "histogram": [{"lo": round(h["lo"], 1), "hi": round(h["lo"] + width, 1), "n": h["n"]} for h in hist],
        "binWidth": width,
        "byArea": [
            {
                "band": r["band"],
                "jeonse": r["jeonse"],
                "jeonseMedianDeposit": rnd(r["jeonse_dep"], 0),
                "jeonseMedianPerM2": rnd(r["jeonse_ppm2"], 1),
                "wolse": r["wolse"],
                "wolseMedianRent": rnd(r["wolse_rent"], 1),
                "wolseMedianDeposit": rnd(r["wolse_dep"], 0),
            }
            for r in await store.by_area(sgg, month, month_end)
        ],
        "notes": [
            "히스토그램·분위수는 전세 계약의 보증금 ㎡당(만원/㎡), 이상치·면적 0 제외. 면적대 중앙값은 이상치만 제외.",
            "면적대 경계는 매매 분포·자체 지수 모형과 같은 40·60·85·135㎡.",
            DEFINITIONS["jeonse"],
        ],
    }, 0  # 집계만 (점 없음)


# ───────────────────────── 계약 목록·이력 ─────────────────────────


@dataclass(frozen=True)
class RentFilter:
    sgg: str
    start: dt.date
    end: dt.date
    min_area: float | None
    max_area: float | None
    kind: str | None  # None = 전체, 'jeonse' | 'wolse'

    def key(self, limit: int) -> dict[str, Any]:
        """캐시 키·커서 지문에 쓰는 질의 모양 (커서는 같은 모양의 질의에서만 유효)."""
        return {
            "sgg": self.sgg,
            "a": self.start.isoformat(),
            "b": self.end.isoformat(),
            "min": self.min_area,
            "max": self.max_area,
            "k": self.kind,
            "l": limit,
        }


def rent_item(r: Row) -> dict[str, Any]:
    jeonse = bool(r["is_jeonse"])
    return {
        "rentId": r["rent_id"],
        "dealDate": r["deal_date"].isoformat(),
        "complex": {
            "complexKey": r["complex_key"],
            "aptName": r["apt_nm"],
            "umdName": r["umd_nm"],
            "jibun": r["jibun"] or None,
            "buildYear": r["build_year"],
        },
        "kind": "jeonse" if jeonse else "wolse",
        "depositManwon": r["deposit_manwon"],
        "monthlyRentManwon": r["monthly_rent_manwon"],
        "areaM2": float(r["area_m2"]),
        "floor": r["floor"],
        "depositPerM2": round(r["deposit_ppm2"], 1) if jeonse and r["deposit_ppm2"] else None,
        "contractType": r["contract_type"] or None,
        "contractTerm": r["contract_term"] or None,
        "renewalRightUsed": r["use_rr_right"] or None,
        "previousDepositManwon": r["pre_deposit_manwon"],
        "previousMonthlyRentManwon": r["pre_monthly_rent_manwon"],
        "outlier": bool(r["is_outlier"]),
        "version": r["version"],
        "missingSince": ts(r["missing_since"]),
    }


def _median(v: float | None) -> float | None:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def read_cursor(key: bytes, token: str | None, fingerprint: str) -> dict[str, Any] | None:
    if not token:
        return None
    pos = cursor.decode(key, token, fingerprint)
    if pos is None:
        raise ApiError(400, "INVALID_CURSOR", "Invalid Cursor", "커서 서명이 맞지 않거나 다른 질의의 커서입니다.")
    return pos


async def page(
    store: RentStore, f: RentFilter, limit: int, pos: dict[str, Any] | None, *, cursor_key: bytes, fingerprint: str
) -> tuple[dict, int]:
    after = (dt.date.fromisoformat(pos["d"]), pos["k"]) if pos else None
    rows = await store.page(f, after, limit + 1)
    summary = None
    if not pos:  # 첫 페이지: 조건 전체 요약
        s = await store.summary(f)
        summary = {
            "count": s["n"],
            "jeonse": s["jeonse"],
            "wolse": s["n"] - s["jeonse"],
            "jeonseMedianDeposit": rnd(_median(s["jeonse_dep"]), 0),
            "jeonseMedianPerM2": rnd(_median(s["jeonse_ppm2"]), 1),
            "wolseMedianRent": rnd(_median(s["wolse_rent"]), 1),
        }
    has_more = len(rows) > limit
    rows = rows[:limit]
    nxt = None
    if has_more and rows:
        last = rows[-1]
        nxt = cursor.encode(cursor_key, {"d": last["deal_date"].isoformat(), "k": last["rent_id"]}, fingerprint)
    return {
        "items": [rent_item(r) for r in rows],
        "summary": summary,
        "page": {"limit": limit, "nextCursor": nxt},
        "definitions": DEFINITIONS,
        "disclaimer": DISCLAIMER,
    }, len(rows)  # 계약 단위 레코드 → 행 한도


def parse_rent_id(rent_id: str) -> int:
    """계약 ID 의 계약월(YYYYMM) — 이력 조회를 그 달 파티션으로 좁힌다."""
    m = RENT_ID.fullmatch(rent_id)
    if not m or not 1 <= int(m.group(2)[4:]) <= 12:
        raise ApiError(400, "INVALID_RENT_ID", "Invalid Rent Id")
    return int(m.group(2))


# 버전 이력에서 바뀜을 추적하는 열 → 응답 이름 (파이프라인 rent_fingerprint.RENT_MUTABLE_FIELDS 와 같다)
TRACKED = {
    "contract_type": "contractType",
    "contract_term": "contractTerm",
    "use_rr_right": "renewalRightUsed",
    "pre_deposit_manwon": "previousDepositManwon",
    "pre_monthly_rent_manwon": "previousMonthlyRentManwon",
    "build_year": "buildYear",
}


def _json(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return ts(v)
    return v if v not in ("",) else None


async def history(store: RentStore, rent_id: str, yyyymm: int) -> tuple[dict, int]:
    rows = await store.versions(rent_id, yyyymm)
    if not rows:
        raise ApiError(404, "RENT_NOT_FOUND", "Not Found")
    versions, prev = [], None
    for r in rows:
        changes: list[dict[str, Any]] = []
        if prev is not None:
            changes = [
                {"field": name, "from": _json(prev[col]), "to": _json(r[col])}
                for col, name in TRACKED.items()
                if prev[col] != r[col]
            ]
        versions.append(
            {
                "version": r["version"],
                "validFrom": ts(r["valid_from"]),
                "validTo": ts(r["valid_to"]),
                "current": bool(r["is_current"]),
                "changes": changes,
                "state": {name: _json(r[col]) for col, name in TRACKED.items()},
            }
        )
        prev = r
    return {"rentId": rent_id, "versions": versions}, len(versions)


# ───────────────────────── 단지 요약 (단지 상세 응답의 rent 블록) ─────────────────────────


def _band(area: float) -> str:
    return next(label for label, lo, hi in COMPLEX_BANDS if lo <= area < hi)


def _med(vals: list[float]) -> float | None:
    """정확 중앙값 — 짝수 개면 가운데 둘의 평균 (ClickHouse quantileExactInclusive(0.5)·dbt 정확 분위수와 같은 정의)."""
    return statistics.median(vals) if vals else None


async def complex_block(store: RentStore, sgg: str, key: str) -> dict[str, Any] | None:
    """최근 12개월(전월세 자료의 마지막 계약월까지) 전용면적 구간별 전세·월세·매매와 단지 전세가율, 최근 계약 10건.
    전월세가 아직 발행되지 않았으면 None."""
    b = await store.latest_month()
    if b is None:
        return None
    a = add_months(b, -(COMPLEX_WINDOW_MONTHS - 1))
    end = add_months(b, 1) - dt.timedelta(days=1)
    rents = await store.complex_rents(sgg, key, a, end)
    trades = await store.complex_trades(sgg, key, a, end)
    bands: dict[str, dict[str, list[float]]] = {}
    for r in rents:
        g = bands.setdefault(_band(float(r["area"])), {"jp": [], "jd": [], "wr": [], "wd": [], "tp": []})
        if r["is_jeonse"]:
            if not r["is_outlier"] and r["area"] > 0:
                g["jp"].append(r["deposit_ppm2"])
            g["jd"].append(float(r["deposit_manwon"]))
        else:
            g["wr"].append(float(r["monthly_rent_manwon"]))
            g["wd"].append(float(r["deposit_manwon"]))
    for t in trades:  # 해제·이상치·면적 0 은 저장소가 이미 뺐다
        bands.setdefault(_band(float(t["area"])), {"jp": [], "jd": [], "wr": [], "wd": [], "tp": []})["tp"].append(
            t["ppm2"]
        )
    by_area = []
    for label, _lo, _hi in COMPLEX_BANDS:
        if label not in bands or not (bands[label]["jd"] or bands[label]["wr"]):
            continue  # 전월세 계약이 없는 구간은 싣지 않는다
        g = bands[label]
        jp, tp = _med(g["jp"]), _med(g["tp"])
        by_area.append(
            {
                "band": label,
                "jeonse": len(g["jd"]),
                "jeonseMedianDeposit": rnd(_med(g["jd"]), 0),
                "jeonseMedianPerM2": rnd(jp, 1),
                "wolse": len(g["wr"]),
                "wolseMedianRent": rnd(_med(g["wr"]), 1),
                "wolseMedianDeposit": rnd(_med(g["wd"]), 0),
                "tradeSample": len(g["tp"]),
                "tradeMedianPerM2": rnd(tp, 1),
                "ratioPct": ratio_pct(jp, tp, len(g["jp"]), len(g["tp"]), COMPLEX_MIN_SAMPLE),
            }
        )
    recent = await store.complex_recent(sgg, key, COMPLEX_RECENT)
    return {
        "window": {"from": a.strftime("%Y-%m"), "to": b.strftime("%Y-%m")},
        "contracts": len(rents),
        "byArea": by_area,
        "recent": [rent_item(r) for r in recent],
        "notes": [COMPLEX_RATIO_NOTE, DEFINITIONS["jeonse"]],
    }
