"""실거래 XML → 원본 문자열 행 → 타입 변환 행 (FR-201).

원천 형식은 W1 스파이크(tools/probe_rtms.py)에서 실제 응답으로 확인했다.
  - 빈 값은 공백 한 칸(" ")으로 온다                      → None
  - dealAmount 는 쉼표 천 단위 문자열("235,000"), 단위 만원 → int
  - cdealDay·rgstDate 는 "YY.MM.DD" ("25.11.27")          → date (20YY)
  - 결과 없음은 <items/> + totalCount 0
상세판(TradeDev, schema_ver 2)은 기본판 20개 항목을 바이트 단위로 같게 주고 12개 항목을 더 준다
(tools/probe_tradedev.py, 2026-10-20 실응답 3개 파티션). 새 항목은 모두 선택 항목이고 지문·속성 해시에 넣지 않는다 (ADR-049).
XML 파싱은 defusedxml 로 한다 (외부 엔티티·엔티티 폭탄 차단).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from defusedxml import ElementTree as SafeET

# 원천 응답 항목 (활용가이드 응답 메시지 명세). 스키마 검사(bronze_schema_matches_v1 — 이름은 이력 때문에 그대로)의 기준.
SOURCE_FIELDS_V1: tuple[str, ...] = (
    "sggCd",
    "umdNm",
    "aptNm",
    "jibun",
    "excluUseAr",
    "dealYear",
    "dealMonth",
    "dealDay",
    "dealAmount",
    "floor",
    "buildYear",
    "cdealType",
    "cdealDay",
    "dealingGbn",
    "estateAgentSggNm",
    "rgstDate",
    "aptDong",
    "slerGbn",
    "buyerGbn",
    "landLeaseholdGbn",
)
# 상세판(TradeDev)에만 있는 항목: 법정동 코드 조각·도로명 주소 조각·단지 일련번호
SOURCE_FIELDS_V2: tuple[str, ...] = (
    *SOURCE_FIELDS_V1,
    "aptSeq",
    "umdCd",
    "landCd",
    "bonbun",
    "bubun",
    "roadNm",
    "roadNmSggCd",
    "roadNmCd",
    "roadNmSeq",
    "roadNmbCd",
    "roadNmBonbun",
    "roadNmBubun",
)
REQUIRED_FIELDS: tuple[str, ...] = ("sggCd", "umdNm", "aptNm", "dealYear", "dealMonth", "dealDay", "dealAmount")
SCHEMA_VERSION = 2  # 지금 받는 판(상세판). bronze.schema_ver 는 행이 실제로 가진 항목으로 정한다 (schema_version_of)
_FIELDS = {1: SOURCE_FIELDS_V1, 2: SOURCE_FIELDS_V2}


def fields_for(schema_ver: int) -> tuple[str, ...]:
    """그 판의 원천 항목 집합. 모르는 판이면 KeyError (검사가 실패로 센다)."""
    return _FIELDS[schema_ver]


def schema_version_of(items: list[dict[str, str]]) -> int | None:
    """항목 집합을 모두 담는 가장 이른 판. 어느 판에도 없는 항목이 있으면 None.
    받은 판을 응답 내용으로 정하므로, 설정으로 기본판에 되돌려도 행의 판이 틀리게 적히지 않는다."""
    keys = {k for i in items for k in i}
    return next((v for v, fields in sorted(_FIELDS.items()) if keys <= set(fields)), None)


class SourceError(Exception):
    """resultCode 가 000 이 아닌 응답 (재시도 대상 여부는 code 로 판단)."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class Page:
    total_count: int
    page_no: int
    num_of_rows: int
    items: list[dict[str, str]]  # 원본 문자열 그대로 (bronze.row_json)


def portal_error_code(xml_text: str) -> str | None:
    """공공데이터포털 게이트웨이 오류 봉투(<OpenAPI_ServiceResponse><cmmMsgHeader>)의 사유 코드.
    예: 일일 한도 초과는 HTTP 429 + returnReasonCode 22 (2026-09 실제 응답으로 확인)."""
    try:
        root = SafeET.fromstring(xml_text)
    except Exception:  # noqa: BLE001 — 봉투가 아니면 None
        return None
    if root.tag != "OpenAPI_ServiceResponse":
        return None
    return (root.findtext("./cmmMsgHeader/returnReasonCode") or "").strip() or None


def parse_page(xml_text: str) -> Page:
    root = SafeET.fromstring(xml_text)
    if root.tag == "OpenAPI_ServiceResponse":
        reason = portal_error_code(xml_text) or "UNKNOWN"
        raise SourceError(reason, (root.findtext("./cmmMsgHeader/errMsg") or "").strip())
    code = (root.findtext("./header/resultCode") or root.findtext(".//resultCode") or "").strip()
    msg = (root.findtext("./header/resultMsg") or root.findtext(".//resultMsg") or "").strip()
    if code not in ("000", "00"):
        raise SourceError(code or "UNKNOWN", msg or xml_text[:200])
    body = root.find("./body")
    if body is None:
        raise SourceError("NO_BODY", "response has no <body>")
    items = [{child.tag: (child.text or "") for child in item} for item in body.findall("./items/item")]
    return Page(
        total_count=int((body.findtext("totalCount") or "0").strip() or 0),
        page_no=int((body.findtext("pageNo") or "1").strip() or 1),
        num_of_rows=int((body.findtext("numOfRows") or "0").strip() or 0),
        items=items,
    )


# ───────────────────────── 값 변환 ─────────────────────────


def blank_to_none(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return s or None


def parse_amount(v: str | None, field: str = "dealAmount", signed: bool = False) -> int | None:
    """쉼표 천 단위 만원 문자열 → 정수. signed=True 면 앞의 '-' 를 받는다 (원천이 음수를 보내는 항목, 원문 그대로)."""
    s = blank_to_none(v)
    if s is None:
        return None
    s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+" if signed else r"\d+", s):
        raise ValueError(f"invalid {field}: {v!r}")
    return int(s)


def parse_int(v: str | None, field: str = "integer") -> int | None:
    """정수 문자열 → 정수. field: 오류에 남길 원천 항목 이름 — 파싱 격리 기록(metric.sample)에서 어느 항목인지 보이게."""
    s = blank_to_none(v)
    if s is None:
        return None
    if not re.fullmatch(r"-?\d+", s):
        raise ValueError(f"invalid {field}: {v!r}")
    return int(s)


def parse_area(v: str | None) -> Decimal | None:
    s = blank_to_none(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"invalid excluUseAr: {v!r}") from e
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def parse_short_date(v: str | None, field: str = "short date") -> dt.date | None:
    """'25.11.27' → 2025-11-27. 원천 제공 범위가 2006년 이후라 세기는 20YY 로 둔다. field: 오류에 남길 원천 항목 이름."""
    s = blank_to_none(v)
    if s is None:
        return None
    m = re.fullmatch(r"(\d{2})\.(\d{1,2})\.(\d{1,2})", s)
    if not m:
        raise ValueError(f"invalid {field}: {v!r}")
    try:
        return dt.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError as e:  # 없는 날짜: 날짜 모듈의 문구엔 항목·원문이 없다
        raise ValueError(f"invalid {field}: {v!r}") from e


def deal_date(year: str | None, month: str | None, day: str | None) -> dt.date:
    y, m, d = parse_int(year, "dealYear"), parse_int(month, "dealMonth"), parse_int(day, "dealDay")
    if y is None or m is None or d is None:
        raise ValueError(f"incomplete deal date: {year!r}-{month!r}-{day!r}")
    try:
        return dt.date(y, m, d)
    except ValueError as e:  # 없는 날짜(13월·2월 30일): 날짜 모듈의 문구엔 원문이 없다
        raise ValueError(f"invalid deal date: {year!r}-{month!r}-{day!r}") from e


@dataclass(frozen=True)
class TypedTrade:
    sgg_cd: str
    umd_nm: str
    apt_nm: str
    jibun: str | None
    area_m2: Decimal | None
    deal_date: dt.date
    price_manwon: int
    floor: int | None
    build_year: int | None
    is_cancelled: bool
    cancel_date: dt.date | None
    registered_date: dt.date | None
    apt_dong: str | None
    deal_kind: str | None
    agent_sgg_nm: str | None
    seller_type: str | None
    buyer_type: str | None
    land_leasehold: bool | None
    # 상세판 보강 항목 (원문 문자열, 빈 값은 None). 지문·속성 해시에 넣지 않는 '보강 전용' — 기본판 행이면 모두 None
    apt_seq: str | None = None
    umd_cd: str | None = None
    land_cd: str | None = None
    bonbun: str | None = None
    bubun: str | None = None
    road_nm: str | None = None
    road_nm_cd: str | None = None
    road_nm_bonbun: str | None = None
    road_nm_bubun: str | None = None


def to_typed(row: dict[str, str]) -> TypedTrade:
    missing = [f for f in REQUIRED_FIELDS if blank_to_none(row.get(f)) is None]
    if missing:
        raise ValueError(f"missing required fields: {missing}")
    price = parse_amount(row.get("dealAmount"))
    assert price is not None
    leasehold = blank_to_none(row.get("landLeaseholdGbn"))
    return TypedTrade(
        sgg_cd=row["sggCd"].strip(),
        umd_nm=row["umdNm"].strip(),
        apt_nm=row["aptNm"].strip(),
        jibun=blank_to_none(row.get("jibun")),
        area_m2=parse_area(row.get("excluUseAr")),
        deal_date=deal_date(row.get("dealYear"), row.get("dealMonth"), row.get("dealDay")),
        price_manwon=price,
        floor=parse_int(row.get("floor"), "floor"),
        build_year=parse_int(row.get("buildYear"), "buildYear"),
        is_cancelled=blank_to_none(row.get("cdealType")) == "O",
        cancel_date=parse_short_date(row.get("cdealDay"), "cdealDay"),
        registered_date=parse_short_date(row.get("rgstDate"), "rgstDate"),
        apt_dong=blank_to_none(row.get("aptDong")),
        deal_kind=blank_to_none(row.get("dealingGbn")),
        agent_sgg_nm=blank_to_none(row.get("estateAgentSggNm")),
        seller_type=blank_to_none(row.get("slerGbn")),
        buyer_type=blank_to_none(row.get("buyerGbn")),
        land_leasehold=None if leasehold is None else leasehold == "Y",
        apt_seq=blank_to_none(row.get("aptSeq")),
        umd_cd=blank_to_none(row.get("umdCd")),
        land_cd=blank_to_none(row.get("landCd")),
        bonbun=blank_to_none(row.get("bonbun")),
        bubun=blank_to_none(row.get("bubun")),
        road_nm=blank_to_none(row.get("roadNm")),
        road_nm_cd=blank_to_none(row.get("roadNmCd")),
        road_nm_bonbun=blank_to_none(row.get("roadNmBonbun")),
        road_nm_bubun=blank_to_none(row.get("roadNmBubun")),
    )
