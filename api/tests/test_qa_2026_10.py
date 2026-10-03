"""2026-10 출시 기준 QA 에서 찾은 결함의 재현 시험. 번호는 docs/qa/2026-10-qa-report.md 의 결함 번호와 같다."""

from __future__ import annotations

import pytest
from helpers import new_key

# QA-002: 형식은 맞지만 달력에 없는 연도(0000)·마지막 달 다음 달이 넘치는 연도(9999-12)가 500 이었다
#         (ValueError: year 0 / 10000 is out of range). 같은 keep-alive 연결의 다음 요청도 끊겼다.
OUT_OF_RANGE_YEAR = [
    ("/v1/market/overview", {"ym": "0000-01"}),
    ("/v1/quality/partitions", {"from": "0000-01", "to": "2024-06"}),
    ("/v1/quality/partitions", {"from": "2024-01", "to": "0000-01"}),
    ("/v1/quality/rollup", {"from": "0000-01", "to": "2024-06"}),
    ("/v1/regions/41135/complexes", {"from": "0000-01", "to": "2024-12"}),
    ("/v1/regions/41135/complexes", {"from": "2024-01", "to": "9999-12"}),
    ("/v1/regions/41135/distribution", {"ym": "0000-01"}),
    ("/v1/regions/41135/distribution", {"ym": "9999-12"}),
    ("/v1/regions/41135/months", {"from": "0000-01", "to": "2024-12"}),
    ("/v1/regions/41135/months", {"from": "2024-01", "to": "0000-01"}),
]


@pytest.mark.parametrize(("path", "params"), OUT_OF_RANGE_YEAR)
async def test_qa_002_out_of_range_year_is_400_not_500(pub, adm, admin_key, path, params):
    key, _ = await new_key(adm, admin_key, "pro")  # 플랜 기간 제한(422)에 가려지지 않게
    r = await pub.get(path, params=params, headers={"X-API-Key": key})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "INVALID_PARAMETER"


# QA-003: 숫자 패턴의 \d 가 유니코드 숫자(전각 '１', 아랍-인도 '١' 등)도 받아 '5자리 숫자' 계약 밖의 값이 200 으로 통과했다
NON_ASCII_DIGITS = [
    ("/v1/regions/１１１１０/months", {"from": "2024-01", "to": "2024-06"}),
    ("/v1/regions/41135/months", {"from": "２０２４-01", "to": "2024-06"}),
    ("/v1/regions/٤١١٣٥/distribution", {"ym": "2024-07"}),
    ("/v1/trades", {"sggCd": "٤١١٣٥", "from": "2024-07-01", "to": "2024-07-31"}),
    ("/v1/index", {"regionId": "٠٠"}),
    ("/v1/quality/partitions", {"from": "2024-01", "to": "2024-06", "sido": "４１"}),
]


@pytest.mark.parametrize(("path", "params"), NON_ASCII_DIGITS)
async def test_qa_003_non_ascii_digits_rejected(pub, adm, admin_key, path, params):
    key, _ = await new_key(adm, admin_key, "pro")
    r = await pub.get(path, params=params, headers={"X-API-Key": key})
    assert r.status_code == 400, (r.status_code, r.text[:200])
    assert r.json()["code"] == "INVALID_PARAMETER"


async def test_qa_003_non_ascii_digits_rejected_in_export_body_and_admin_path(pub, adm, admin_key):
    key, _ = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    r = await pub.post(
        "/v1/exports",
        json={"from": "2024-07-01", "to": "2024-07-31", "sggCd": "４１１３５"},
        headers={"X-API-Key": key},
    )
    assert r.status_code == 400, (r.status_code, r.text[:200])
    r = await adm.post("/v1/admin/partitions/４１１３５/2024-07/retry", headers={"X-API-Key": admin_key})
    assert r.status_code == 400, (r.status_code, r.text[:200])
