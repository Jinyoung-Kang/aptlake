"""원천 클라이언트 오류 분류: 한도 초과·인증키 오류는 실행 중단(격리 아님), 요청 오류는 즉시 실패, 5xx 는 재시도."""

import asyncio

import httpx
import pytest

from aptlake_pipeline.rtms.client import FetchFailed, KeyRejected, QuotaExceeded, RtmsClient, SourceUnavailable

QUOTA = (
    '<?xml version="1.0"?><OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>LIMITED</errMsg>'
    "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
)
BAD_KEY = (
    '<?xml version="1.0"?><OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg>'
    "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
)
OK = (
    "<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header><body><items/>"
    "<numOfRows>9999</numOfRows><pageNo>1</pageNo><totalCount>0</totalCount></body></response>"
)


def run(responses: list[httpx.Response]):
    calls = []
    it = iter(responses)

    def handler(request):
        calls.append(request)
        return next(it)

    client = RtmsClient(
        "k",
        reserve=lambda n: None,
        tps=1000,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        base_delay=0,
    )
    return asyncio.run(client.fetch("11110", "202401")), calls


def test_http_429_quota_raises_quota_exceeded():
    with pytest.raises(QuotaExceeded):
        run([httpx.Response(429, text=QUOTA)])


def test_bad_key_stops_the_run_instead_of_quarantining_partitions():
    # 키가 만료·미등록이면 모든 호출이 같은 오류 → 파티션마다 격리하지 않고 실행을 멈춘다
    with pytest.raises(KeyRejected):
        run([httpx.Response(401, text=BAD_KEY)])
    with pytest.raises(KeyRejected):  # 포털이 200 + 오류 봉투로 줄 때도
        run([httpx.Response(200, text=BAD_KEY)])
    with pytest.raises(KeyRejected):  # 사유 코드 없는 403
        run([httpx.Response(403, text="forbidden")])
    assert issubclass(QuotaExceeded, SourceUnavailable) and issubclass(KeyRejected, SourceUnavailable)


def test_request_error_fails_only_that_partition():
    bad_param = BAD_KEY.replace("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "INVALID_REQUEST_PARAMETER_ERROR").replace(
        ">30<", ">10<"
    )
    with pytest.raises(FetchFailed) as e:
        run([httpx.Response(400, text=bad_param)])
    assert e.value.code == "10"


def test_5xx_is_retried_then_succeeds():
    result, calls = run([httpx.Response(502, text="bad gateway"), httpx.Response(200, text=OK)])
    assert len(calls) == 2 and result.items == []


def test_http_errors_never_carry_the_api_key():
    from aptlake_pipeline.http_safe import SourceHTTPError, checked

    req = httpx.Request("GET", "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList?serviceKey=SECRETKEY")
    with pytest.raises(SourceHTTPError) as e:
        checked(httpx.Response(500, request=req), "MOIS")
    assert "SECRETKEY" not in str(e.value) and "500" in str(e.value)
