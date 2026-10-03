"""수집 상태·오류 로그·확정 월 지수 단위 테스트 (컨테이너 없음).

- 오류 로그는 공개 화면에 나가므로 비밀값 가림이 핵심 계약
- Dagster GraphQL 응답 해석 (이벤트 시각은 밀리초, 실행 시각은 초 — 둘을 섞어 500 이 났던 회귀)
- 지수 대표값은 잠정이 아닌 최근 달
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from aptlake_api import ops
from aptlake_api.routes_market import _add, _index_point, provisional

FAKE_KEY_SECRET = "Zq9" * 14 + "Z"  # 43자 — 소스에 키 형식 문자열을 통째로 두지 않는다 (gitleaks 규칙과 충돌 방지)
SECRETS = {
    "https://apis.data.go.kr/1613000/RTMS?serviceKey=AbC%2B123==&LAWD_CD=11110": "AbC%2B123==",
    "https://api.vworld.kr/req/data?service=data&key=VW-KEY-1234&domain=localhost": "VW-KEY-1234",
    "conninfo postgresql+psycopg://pipeline:s3cr3t@postgres:5432/aptlake failed": "s3cr3t",
    "redis://:redispw@redis:6379/0": "redispw",
    "X-API-Key: al_live_ABCDEFGHJKLM." + FAKE_KEY_SECRET: FAKE_KEY_SECRET,
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig": "eyJhbGciOiJIUzI1NiJ9",
    "{'s3.secret-access-key': 'wJalrXUtnFEMI', 's3.access-key-id': 'AKIAIOSFODNN7'}": "wJalrXUtnFEMI",
    "aws_session_token=FwoGZXIvYXdzEJr": "FwoGZXIvYXdzEJr",
    "GET /b/k?X-Amz-Credential=AKIA%2F2026&X-Amz-Signature=abcdef012345": "abcdef012345",
    '{"password": "hunter2"}': "hunter2",
}


@pytest.mark.parametrize("raw", list(SECRETS))
def test_redact_hides_secrets(raw):
    out = ops.redact(raw)
    assert SECRETS[raw] not in out
    assert "***" in out


def test_redact_keeps_identifiers_that_help_debugging():
    raw = '{"complex_key": "c_aaaaaaaaaaaaaaaaaaaa", "stepKey": "bronze", "partition_key": "202407"}'
    assert ops.redact(raw) == raw
    assert ops.redact(None) == ""


def test_iso_units_and_bad_values():
    assert ops.iso(1790387026.44) == "2026-09-26T01:43:46.440000+00:00"
    assert ops.iso("1790387026") == "2026-09-26T01:43:46+00:00"
    assert ops.iso(None) is None and ops.iso("") is None
    assert ops.iso(1790385472373) is None  # 밀리초를 초로 잘못 넘기면 버린다 (목록 전체 500 대신)
    assert ops._event_ts({"timestamp": "1790385472373"}) == pytest.approx(1790385472.373)


def _gql(handler):
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": handler(body["query"], body.get("variables") or {})})

    return ops.DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(respond)))


async def test_run_errors_prefers_step_failures_and_caches():
    calls = []

    def handler(query, variables):
        calls.append(variables["id"])
        return {
            "logsForRun": {
                "__typename": "EventConnection",
                "events": [
                    {"__typename": "RunStartEvent", "timestamp": "1790385472373"},
                    {
                        "__typename": "ExecutionStepFailureEvent",
                        "timestamp": "1790385480000",
                        "stepKey": "bronze__rtms",
                        "error": {
                            "className": "HTTPError",
                            "message": "429 for url https://x?serviceKey=SECRET",
                            "stack": [f"frame {i}\n" for i in range(30)],
                            "causes": [{"className": "ReadTimeout", "message": "token=abc"}],
                        },
                    },
                    {"__typename": "RunFailureEvent", "timestamp": "1790385481000", "error": None},
                ],
            }
        }

    dag = _gql(handler)
    errs = await dag.run_errors("run-1")
    assert len(errs) == 1 and errs[0]["step"] == "bronze__rtms"
    await dag.run_errors("run-1")
    assert calls == ["run-1"]  # 끝난 실행의 오류는 다시 묻지 않는다

    job = ops.job_row(
        {
            "runId": "run-1",
            "jobName": "month_pipeline",
            "status": "FAILURE",
            "creationTime": 1790385470.0,
            "startTime": 1790385471.0,
            "endTime": 1790385482.5,
            "tags": [{"key": "dagster/partition", "value": "202407"}, {"key": "aptlake/priority", "value": "retry"}],
        }
    )
    assert job["durationS"] == 11.5 and job["priorityLabel"] == "재시도" and job["trigger"] == "수동"
    [e] = ops.run_error_entries(job, errs)
    assert e["at"] == "2026-09-26T01:18:00+00:00"
    assert e["where"] == "월 수집·반영·발행 · 202407 · bronze__rtms"
    assert "SECRET" not in e["message"] + e["detail"] and "abc" not in e["detail"]
    assert "frame 29" in e["detail"] and "frame 10" not in e["detail"]  # 스택은 끝쪽 12프레임만
    await dag.aclose()


async def test_run_errors_falls_back_to_run_failure():
    dag = _gql(
        lambda q, v: {
            "logsForRun": {
                "__typename": "EventConnection",
                "events": [
                    {
                        "__typename": "RunFailureEvent",
                        "timestamp": "1790385481000",
                        "message": "orphaned run cleaned up at startup",
                        "error": None,
                    }
                ],
            }
        }
    )
    [e] = await dag.run_errors("run-2")
    assert e["step"] is None and e["error"]["message"] == "orphaned run cleaned up at startup"
    await dag.aclose()


async def test_graphql_errors_become_unavailable():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Limit of 2000 is too large"}]})

    dag = ops.DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    with pytest.raises(ops.DagsterUnavailable):
        await dag.run_errors("run-3")
    assert "run-3" not in dag._run_errors  # 실패는 캐시하지 않는다
    await dag.aclose()


def test_index_point_confirmed_month(monkeypatch):
    from aptlake_api.core import clock

    monkeypatch.setattr(clock, "settings", lambda: SimpleNamespace(provisional_days=60))
    this = clock.kst_today().replace(day=1)
    old = _add(this, -12)
    assert provisional(this) and not provisional(old)
    by = {_add(old, -12): 100.0, _add(old, -1): 110.0, old: 121.0}
    assert _index_point(by, old) == {
        "period": f"{old:%Y-%m}",
        "value": 121.0,
        "provisional": False,
        "mom": 10.0,
        "yoy": 21.0,
    }
    assert _index_point({this: 99.0}, this)["mom"] is None  # 앞 달이 없으면 변화율 없음
