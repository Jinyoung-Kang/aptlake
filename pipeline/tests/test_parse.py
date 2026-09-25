import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from aptlake_pipeline.rtms.parse import (
    SOURCE_FIELDS_V1,
    SourceError,
    blank_to_none,
    deal_date,
    parse_amount,
    parse_area,
    parse_int,
    parse_page,
    parse_short_date,
    to_typed,
)

FIX = Path(__file__).parent / "fixtures"


def test_real_page_parses_all_items():
    page = parse_page((FIX / "rtms_11110_202407.xml").read_text())
    assert page.total_count == 60
    assert len(page.items) == 60
    # 계약: 실제 응답의 필드 집합이 명세 v1 과 같다 (원천 스키마 변경 감지)
    assert all(set(i) == set(SOURCE_FIELDS_V1) for i in page.items)
    typed = [to_typed(i) for i in page.items]
    assert all(t.sgg_cd == "11110" and t.deal_date.strftime("%Y%m") == "202407" for t in typed)
    assert any(t.is_cancelled for t in typed)
    assert all((t.cancel_date is not None) == t.is_cancelled for t in typed)


def test_empty_page():
    page = parse_page((FIX / "rtms_empty.xml").read_text())
    assert page.total_count == 0 and page.items == []


def test_error_result_code_raises():
    xml = "<response><header><resultCode>22</resultCode><resultMsg>LIMITED</resultMsg></header></response>"
    with pytest.raises(SourceError) as e:
        parse_page(xml)
    assert e.value.code == "22"


def test_xml_entity_expansion_is_rejected():
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
        "<response><header><resultCode>&b;</resultCode></header></response>"
    )
    with pytest.raises(Exception) as e:
        parse_page(bomb)
    assert "Entities" in type(e.value).__name__ or "entit" in str(e.value).lower()


@pytest.mark.parametrize(
    "raw,expected", [("12,000", 12000), ("235,000", 235000), (" 9,500 ", 9500), (" ", None), ("", None)]
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["12,0a0", "-3", "1.5"])
def test_parse_amount_rejects_garbage(raw):
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_blank_and_ints():
    assert blank_to_none(" ") is None and blank_to_none(" x ") == "x"
    assert parse_int("-1") == -1 and parse_int(" ") is None
    with pytest.raises(ValueError):
        parse_int("1층")


def test_area_quantized_to_4dp():
    assert parse_area("17.811") == Decimal("17.8110")
    assert parse_area("84.99995") == Decimal("85.0000")
    assert parse_area(" ") is None


def test_short_date():
    assert parse_short_date("25.11.27") == dt.date(2025, 11, 27)
    assert parse_short_date("24.7.5") == dt.date(2024, 7, 5)
    assert parse_short_date(" ") is None
    with pytest.raises(ValueError):
        parse_short_date("2025-11-27")


def test_deal_date_combines_parts():
    assert deal_date("2024", "7", "23") == dt.date(2024, 7, 23)
    with pytest.raises(ValueError):
        deal_date("2024", " ", "1")
    with pytest.raises(ValueError):
        deal_date("2024", "2", "30")


def test_required_field_missing():
    row = {
        "sggCd": "11110",
        "umdNm": "숭인동",
        "aptNm": " ",
        "dealYear": "2024",
        "dealMonth": "7",
        "dealDay": "1",
        "dealAmount": "1,000",
    }
    with pytest.raises(ValueError, match="aptNm"):
        to_typed(row)


def test_portal_quota_envelope_is_recognised():
    """2026-09 실제 응답: HTTP 429 + 포털 오류 봉투, 사유 코드 22 (일일 한도 초과)."""
    from aptlake_pipeline.rtms.parse import portal_error_code

    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<OpenAPI_ServiceResponse>\n<cmmMsgHeader>\n'
        "  <errMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</errMsg>\n"
        "  <returnReasonCode>22</returnReasonCode>\n</cmmMsgHeader>\n</OpenAPI_ServiceResponse>\n"
    )
    assert portal_error_code(body) == "22"
    with pytest.raises(SourceError) as e:
        parse_page(body)
    assert e.value.code == "22"
    assert portal_error_code("<response/>") is None
    assert portal_error_code("not xml") is None
