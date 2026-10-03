"""단지: 상세(단지 정보·최근 거래·거래 이력)와 단지명·법정동 검색 — 업무 규칙 (순수)."""

from __future__ import annotations

from typing import Any, Protocol

from ...core.problems import ApiError
from ..trades.service import trade_item

Row = dict[str, Any]
RECENT = 20  # 상세의 최근 거래 수
HISTORY = 1000  # 상세의 거래 이력(산점도) 점 상한
SEARCH_LIMIT = 15


class ComplexStore(Protocol):
    async def get(self, key: str) -> Row | None: ...
    async def recent_trades(self, sgg: str, key: str, limit: int) -> list[Row]: ...
    async def trade_points(self, sgg: str, key: str, limit: int) -> list[Row]: ...
    async def search(self, term: str, limit: int) -> list[Row]: ...


async def detail(store: ComplexStore, key: str) -> tuple[dict, int]:
    cx = await store.get(key)
    if cx is None:
        raise ApiError(404, "COMPLEX_NOT_FOUND", "Not Found")
    recent = await store.recent_trades(cx["sgg_cd"], key, RECENT)
    history = await store.trade_points(cx["sgg_cd"], key, HISTORY)
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
        "recentTrades": [trade_item(r) for r in recent],
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
    }, 1 + len(recent) + len(history)  # 거래 단위 레코드 → 행 한도


def search_term(q: str) -> str:
    term = q.strip()
    if not term:
        raise ApiError(400, "INVALID_QUERY", "Invalid Query")
    return term


async def search(store: ComplexStore, term: str) -> tuple[dict, int]:
    return {
        "complexes": [
            {
                "complexKey": r["complex_key"],
                "sggCd": r["sgg_cd"],
                "umdName": r["umd_nm"],
                "aptName": r["apt_nm"],
                "buildYear": r["build_year"],
                "validTrades": r["trades"],
            }
            for r in await store.search(term, SEARCH_LIMIT)
        ]
    }, 0
