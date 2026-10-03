"""거래: 목록(서명된 커서 페이지 + 첫 페이지 요약)과 버전 이력(SCD2) — 업무 규칙 (순수)."""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ...core import cursor
from ...core.problems import ApiError
from ...core.texts import DISCLAIMER
from ...core.values import rnd, ts

Row = dict[str, Any]
TRADE_ID = re.compile(r"^(\d{5})-(\d{6})-[0-9a-f]{16}-\d{1,4}$")
# 버전 이력에서 바뀜을 추적하는 열 → 응답 이름
TRACKED = {
    "is_cancelled": "cancelled",
    "cancel_date": "cancelDate",
    "registered_date": "registeredDate",
    "apt_dong": "aptDong",
    "deal_kind": "dealKind",
    "seller_type": "sellerType",
    "buyer_type": "buyerType",
}


@dataclass(frozen=True)
class TradeFilter:
    sgg: str
    start: dt.date
    end: dt.date
    min_area: float | None
    max_area: float | None
    include_cancelled: bool

    def key(self, limit: int) -> dict[str, Any]:
        """캐시 키·커서 지문에 쓰는 질의 모양 (커서는 같은 모양의 질의에서만 유효)."""
        return {
            "sgg": self.sgg,
            "a": self.start.isoformat(),
            "b": self.end.isoformat(),
            "min": self.min_area,
            "max": self.max_area,
            "c": self.include_cancelled,
            "l": limit,
        }


class TradeStore(Protocol):
    async def page(self, f: TradeFilter, after: tuple[dt.date, str] | None, limit: int) -> list[Row]: ...
    async def summary(self, f: TradeFilter) -> Row: ...
    async def versions(self, trade_id: str, yyyymm: int) -> list[Row]: ...


def check_page_size(plan_id: str, max_page_size: int, limit: int) -> None:
    if limit > max_page_size:
        raise ApiError(
            422, "PAGE_SIZE_EXCEEDS_PLAN", "Page Too Large", f"{plan_id} 플랜의 limit 상한은 {max_page_size}"
        )


def read_cursor(key: bytes, token: str | None, fingerprint: str) -> dict[str, Any] | None:
    if not token:
        return None
    pos = cursor.decode(key, token, fingerprint)
    if pos is None:
        raise ApiError(400, "INVALID_CURSOR", "Invalid Cursor", "커서 서명이 맞지 않거나 다른 질의의 커서입니다.")
    return pos


def trade_item(r: Row) -> dict[str, Any]:
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
        "missingSince": ts(r["missing_since"]),
    }


def _summary(a0: Row) -> dict[str, Any]:
    med_price = a0["med_price"]
    # 조건의 거래가 모두 해제면 유효 거래가 없어 중위가가 NaN — round(NaN) 은 예외라 '없음'으로
    no_price = a0["n"] == 0 or med_price is None or (isinstance(med_price, float) and math.isnan(med_price))
    return {
        "count": a0["n"],
        "cancelled": a0["cancelled"],
        "medianPpm2": rnd(a0["med"]),
        "medianPrice": None if no_price else round(med_price),
    }


async def page(
    store: TradeStore, f: TradeFilter, limit: int, pos: dict[str, Any] | None, *, cursor_key: bytes, fingerprint: str
) -> tuple[dict, int]:
    after = (dt.date.fromisoformat(pos["d"]), pos["k"]) if pos else None
    rows = await store.page(f, after, limit + 1)
    # 첫 페이지: 조건 전체 요약 (불러온 페이지가 아니라 전체 조건 기준)
    summary = None if pos else _summary(await store.summary(f))
    has_more = len(rows) > limit
    rows = rows[:limit]
    nxt = None
    if has_more and rows:
        last = rows[-1]
        nxt = cursor.encode(cursor_key, {"d": last["deal_date"].isoformat(), "k": last["trade_id"]}, fingerprint)
    return {
        "items": [trade_item(r) for r in rows],
        "summary": summary,
        "page": {"limit": limit, "nextCursor": nxt},
        "disclaimer": DISCLAIMER,
    }, len(rows)


def parse_trade_id(trade_id: str) -> int:
    """거래 ID 의 계약월(YYYYMM) — 이력 조회를 그 달 파티션으로 좁힌다."""
    m = TRADE_ID.fullmatch(trade_id)
    if not m or not 1 <= int(m.group(2)[4:]) <= 12:  # 형식은 맞아도 없는 달(13월·0월)이면 잘못된 ID
        raise ApiError(400, "INVALID_TRADE_ID", "Invalid Trade Id")
    return int(m.group(2))


def _json(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return ts(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, int) and v in (0, 1) and not isinstance(v, bool):
        return bool(v)
    return v or None


async def history(store: TradeStore, trade_id: str, yyyymm: int) -> tuple[dict, int]:
    rows = await store.versions(trade_id, yyyymm)
    if not rows:
        raise ApiError(404, "TRADE_NOT_FOUND", "Not Found")
    versions, prev = [], None
    for r in rows:
        changes: list[dict[str, Any]] = []
        if prev is not None:
            for col, name in TRACKED.items():
                if prev[col] != r[col]:
                    changes.append({"field": name, "from": _json(prev[col]), "to": _json(r[col])})
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
    return {"tradeId": trade_id, "versions": versions}, len(versions)
