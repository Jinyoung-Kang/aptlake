"""원천 설정 — 매매·전월세가 같은 수집·검사·적재 코드를 쓰도록 다른 점만 모은다 (ADR-050).

원천 이름(name)은 운영 상태(ops.ingest_partition.source)·호출 예산(budget:{name}:{day})·품질 기록의 이름이다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .rtms import parse, rent_parse
from .rtms.client import RENT_URL
from .rtms.fingerprint import norm_jibun, norm_text


@dataclass(frozen=True)
class SourceSpec:
    name: str
    bronze: str  # bronze 표 (스키마는 원천 공통: row_json + schema_ver)
    raw_prefix: str  # raw 버킷 안 원본 XML 접두어
    latest_version: int  # 지금 받는 판 — 이 판의 항목 밖은 원천 스키마 변경
    fields_for: Callable[[int], tuple[str, ...]]
    version_of: Callable[[list[dict[str, str]]], int | None]
    required: tuple[str, ...]
    to_typed: Callable[[dict[str, str]], Any]
    # 법정동 이름을 뺀 지문 재료 — 같은 파티션 직전 원본과 비교해 '이름만 바뀐 같은 행'을 찾는다 (ADR-042)
    identity: Callable[[Any], tuple]
    url: Callable[[Any], str]  # 설정 → 원천 주소


def _area(t: Any) -> str:
    return "" if t.area_m2 is None else f"{t.area_m2:.4f}"


def _trade_identity(t: parse.TypedTrade) -> tuple:
    return (t.sgg_cd, norm_jibun(t.jibun), norm_text(t.apt_nm), _area(t), t.deal_date, t.price_manwon, t.floor)


def _rent_identity(t: rent_parse.TypedRent) -> tuple:
    return (t.sgg_cd, norm_jibun(t.jibun), norm_text(t.apt_nm), _area(t), t.deal_date, t.deposit_manwon,
            t.monthly_rent_manwon, t.floor)  # fmt: skip


TRADE = SourceSpec(
    name="rtms",
    bronze="bronze.rtms_raw",
    raw_prefix="rtms",
    latest_version=parse.SCHEMA_VERSION,
    fields_for=parse.fields_for,
    version_of=parse.schema_version_of,
    required=parse.REQUIRED_FIELDS,
    to_typed=parse.to_typed,
    identity=_trade_identity,
    url=lambda s: s.rtms_trade_url,
)
RENT = SourceSpec(
    name="rent",
    bronze="bronze.rtms_rent_raw",
    raw_prefix="rtms_rent",
    latest_version=rent_parse.RENT_SCHEMA_VERSION,
    fields_for=rent_parse.rent_fields_for,
    version_of=rent_parse.rent_schema_version_of,
    required=rent_parse.REQUIRED_RENT_FIELDS,
    to_typed=rent_parse.to_typed_rent,
    identity=_rent_identity,
    url=lambda s: RENT_URL,
)
