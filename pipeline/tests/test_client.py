"""원천 클라이언트 오류 분류: 한도 초과는 실행 중단(격리 아님), 키 오류는 즉시 실패, 5xx 는 재시도."""

import asyncio

import httpx
import pytest

from aptlake_pipeline.rtms.client import FetchFailed, QuotaExceeded, RtmsClient

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


def test_bad_key_fails_fast_with_reason_code():
    with pytest.raises(FetchFailed) as e:
        run([httpx.Response(401, text=BAD_KEY)])
    assert e.value.code == "30"


def test_5xx_is_retried_then_succeeds():
    result, calls = run([httpx.Response(502, text="bad gateway"), httpx.Response(200, text=OK)])
    assert len(calls) == 2 and result.items == []
