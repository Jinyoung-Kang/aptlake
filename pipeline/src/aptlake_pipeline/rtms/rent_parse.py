"""아파트 전월세 실거래 XML 행 → 타입 변환 행 (ADR-050).

원천 형식은 0단계 탐침(tools/probe_rent.py, 2026-10-20 실응답 6개 파티션)으로 확인했다.
  - 항목 25개. 해제 항목이 없다. 도로명 항목 이름은 소문자(roadnm, roadnmbcd …) — 매매 상세판(roadNm …)과 다르다
  - 빈 값은 공백 한 칸(" "), 도로명 건물 코드(roadnmbcd)만 빈 문자열("")             → None
  - deposit·preDeposit 은 쉼표 천 단위 만원 문자열("43,500"), monthlyRent 는 빈 값 없이 늘 숫자(0 포함)
  - contractType 은 '신규'·'갱신'·공백, useRRRight 는 '사용'·공백, contractTerm 은 'YY.MM~YY.MM'(드물게 '~YY.MM')
값의 뜻은 원천 낱말 그대로 쓴다 — 원문을 보관하고 코드로 바꾸지 않는다(ADR-018). 공백은 '미상'.
페이지 봉투·오류 코드는 매매와 같다 (parse.parse_page).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from .parse import blank_to_none, deal_date, parse_amount, parse_area, parse_int

RENT_FIELDS_V1: tuple[str, ...] = (
    "sggCd",
    "umdNm",
    "aptNm",
    "jibun",
    "excluUseAr",
    "dealYear",
    "dealMonth",
    "dealDay",
    "deposit",
    "monthlyRent",
    "floor",
    "buildYear",
    "contractTerm",
    "contractType",
    "useRRRight",
    "preDeposit",
    "preMonthlyRent",
    "aptSeq",
    "roadnm",
    "roadnmsggcd",
    "roadnmcd",
    "roadnmseq",
    "roadnmbcd",
    "roadnmbonbun",
    "roadnmbubun",
)
# 월세가 빈 값이면 전세·월세를 가를 수 없다 — 탐침에서는 한 번도 비지 않았다
REQUIRED_RENT_FIELDS: tuple[str, ...] = (
    "sggCd",
    "umdNm",
    "aptNm",
    "dealYear",
    "dealMonth",
    "dealDay",
    "deposit",
    "monthlyRent",
)
RENT_SCHEMA_VERSION = 1
_FIELDS = {1: RENT_FIELDS_V1}


def rent_fields_for(schema_ver: int) -> tuple[str, ...]:
    return _FIELDS[schema_ver]


def rent_schema_version_of(items: list[dict[str, str]]) -> int | None:
    keys = {k for i in items for k in i}
    return next((v for v, fields in sorted(_FIELDS.items()) if keys <= set(fields)), None)


@dataclass(frozen=True)
class TypedRent:
    sgg_cd: str
    umd_nm: str
    apt_nm: str
    jibun: str | None
    area_m2: Decimal | None
    deal_date: dt.date  # 계약일 (원천 dealYear·dealMonth·dealDay)
    deposit_manwon: int
    monthly_rent_manwon: int
    floor: int | None
    build_year: int | None
    contract_type: str | None  # 원문: '신규'·'갱신' (공백 = 미상)
    contract_term: str | None  # 원문: 'YY.MM~YY.MM'
    use_rr_right: str | None  # 원문: '사용' (공백 = 미상·미사용 구분 없음)
    pre_deposit_manwon: int | None
    pre_monthly_rent_manwon: int | None
    # 보강 전용 (지문·속성 해시 밖): 단지 일련번호, 도로명 주소 원문('송월길 99' — 건물 번호 포함)
    apt_seq: str | None = None
    road_nm: str | None = None


def to_typed_rent(row: dict[str, str]) -> TypedRent:
    missing = [f for f in REQUIRED_RENT_FIELDS if blank_to_none(row.get(f)) is None]
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    deposit = parse_amount(row.get("deposit"))
    rent = parse_amount(row.get("monthlyRent"))
    assert deposit is not None and rent is not None
    return TypedRent(
        sgg_cd=row["sggCd"].strip(),
        umd_nm=row["umdNm"].strip(),
        apt_nm=row["aptNm"].strip(),
        jibun=blank_to_none(row.get("jibun")),
        area_m2=parse_area(row.get("excluUseAr")),
        deal_date=deal_date(row.get("dealYear"), row.get("dealMonth"), row.get("dealDay")),
        deposit_manwon=deposit,
        monthly_rent_manwon=rent,
        floor=parse_int(row.get("floor")),
        build_year=parse_int(row.get("buildYear")),
        contract_type=blank_to_none(row.get("contractType")),
        contract_term=blank_to_none(row.get("contractTerm")),
        use_rr_right=blank_to_none(row.get("useRRRight")),
        pre_deposit_manwon=parse_amount(row.get("preDeposit")),
        pre_monthly_rent_manwon=parse_amount(row.get("preMonthlyRent")),
        apt_seq=blank_to_none(row.get("aptSeq")),
        road_nm=blank_to_none(row.get("roadnm")),
    )
