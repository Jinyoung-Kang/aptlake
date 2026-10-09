"""변경분 피드의 비교 규칙 (ADR-045, 데이터 제품 명세 2.4) — 순수 함수.

달을 발행할 때, 그 달의 '마지막으로 기록된 스냅숏 파일'(old)과 곧 교체될 새 값(new)을 거래 ID 로 맞대어 본다.
비교 기준이 서빙의 지금 값이 아니라 기록된 파일이라, 교체 뒤 버전 기록만 실패한 발행이 있어도 그 변경은 다음 발행에 함께 나온다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# 비교하는 열 → 응답(거래 목록 trade_item)의 필드 이름. 식별 열(trade_id·sgg_cd·deal_date)과
# 버전과 함께만 바뀌는 valid_from 은 비교하지 않는다
FIELDS = {
    "complex_key": "complex.complexKey",
    "apt_nm": "complex.aptName",
    "umd_nm": "complex.umdName",
    "jibun": "complex.jibun",
    "build_year": "complex.buildYear",
    "area_m2": "areaM2",
    "floor": "floor",
    "price_manwon": "priceManwon",
    "ppm2": "pricePerM2",
    "is_cancelled": "cancelled",
    "cancel_date": "cancelDate",
    "registered_date": "registeredDate",
    "apt_dong": "aptDong",
    "deal_kind": "dealKind",
    "seller_type": "sellerType",
    "buyer_type": "buyerType",
    "is_outlier": "outlier",
    "version": "version",
    "missing_since": "missingSince",
}


@dataclass(frozen=True)
class Change:
    trade_id: str
    change: str  # added | removed | updated | missing | restored
    changed_fields: tuple[str, ...]
    row: Mapping[str, Any]  # 새 값 (removed 면 직전 값)


def diff_month(old: Iterable[Mapping[str, Any]], new: Iterable[Mapping[str, Any]]) -> list[Change]:
    """거래 ID 순으로 변경 목록. 바뀐 것이 없으면 빈 목록."""
    before = {r["trade_id"]: r for r in old}
    after = {r["trade_id"]: r for r in new}
    out: list[Change] = []
    for tid in sorted(before.keys() | after.keys()):
        a, b = before.get(tid), after.get(tid)
        if a is None and b is not None:
            out.append(Change(tid, "added", (), b))
        elif b is None and a is not None:
            out.append(Change(tid, "removed", (), a))
        elif a is not None and b is not None:
            diff = [c for c in FIELDS if a.get(c) != b.get(c)]
            if not diff:
                continue
            if diff == ["missing_since"]:  # 원천에서 사라짐·다시 나타남만 바뀜
                if a["missing_since"] is None:
                    out.append(Change(tid, "missing", (), b))
                    continue
                if b["missing_since"] is None:
                    out.append(Change(tid, "restored", (), b))
                    continue
            out.append(Change(tid, "updated", tuple(FIELDS[c] for c in diff), b))
    return out
