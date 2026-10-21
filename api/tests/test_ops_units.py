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

from aptlake_api.core.clock import provisional
from aptlake_api.core.redact import redact
from aptlake_api.core.values import add_months as _add
from aptlake_api.features.index.service import index_point
from aptlake_api.features.ops import service as ops
from aptlake_api.features.ops.dagster import DagsterClient, _event_ts

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
    out = redact(raw)
    assert SECRETS[raw] not in out
    assert "***" in out


def test_redact_keeps_identifiers_that_help_debugging():
    raw = '{"complex_key": "c_aaaaaaaaaaaaaaaaaaaa", "stepKey": "bronze", "partition_key": "202407"}'
    assert redact(raw) == raw
    assert redact(None) == ""


def test_iso_units_and_bad_values():
    assert ops.epoch_iso(1790387026.44) == "2026-09-26T01:43:46.440000+00:00"
    assert ops.epoch_iso("1790387026") == "2026-09-26T01:43:46+00:00"
    assert ops.epoch_iso(None) is None and ops.epoch_iso("") is None
    assert ops.epoch_iso(1790385472373) is None  # 밀리초를 초로 잘못 넘기면 버린다 (목록 전체 500 대신)
    assert _event_ts({"timestamp": "1790385472373"}) == pytest.approx(1790385472.373)


def _gql(handler):
    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": handler(body["query"], body.get("variables") or {})})

    return DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(respond)))


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

    dag = DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
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
    assert index_point(by, old, is_provisional=provisional) == {
        "period": f"{old:%Y-%m}",
        "value": 121.0,
        "provisional": False,
        "mom": 10.0,
        "yoy": 21.0,
    }
    assert index_point({this: 99.0}, this, is_provisional=provisional)["mom"] is None  # 앞 달이 없으면 변화율 없음


@pytest.mark.parametrize(
    "pending,share,days",
    [
        # 운영 실측 (전월세, 수집 대상 시군구 256): 2026-10-20 재확인 대기 중 백필 4,096 ≤ 4,160,
        # 2026-10-21 재확인 대기 없음 백필 7,232 (+ 증분 768 = 상한 8,000)
        (True, 8000 - 256 * 3 - 256 * 12, 1),
        (False, 8000 - 256 * 3, 1),
    ],
)
def test_backfill_estimate_divides_by_the_backfill_share_not_the_cap(pending, share, days):
    """백필은 상한에서 증분 몫(시군구×3)과, 재확인 대기 중이면 재확인 몫(시군구×12)을 남기고 쓴다 (파이프라인 budget.ceilings)."""
    assert ops.backfill_share(8000, 256, pending) == share
    est = ops._estimate(
        {"PENDING": 3570, "RETRY": 462, "MERGED": 13873}, 8000, {"regions": 256, "recheck_pending": pending}
    )
    assert est["remainingCalls"] == 4032 and est["days"] == days
    assert f"하루 백필 몫 {share:,}회" in est["basis"] and "증분 몫 768(시군구 256 × 3개월)" in est["basis"]
    assert ("재확인 몫 3,072(시군구 256 × 12개월, 재확인 대기 중)" in est["basis"]) is pending
    # 백필 몫으로 나눈다 — 상한(8,000)으로 나누면 1일인 남은 양이 백필 몫으로는 2일
    assert ops._estimate({"PENDING": 8000}, 8000, {"regions": 256, "recheck_pending": pending})["days"] == 2


def test_backfill_estimate_without_share_or_remaining():
    assert ops.backfill_share(500, 256, True) == 0  # 몫이 남지 않으면 백필은 못 한다
    assert ops._estimate({"PENDING": 10}, 500, {"regions": 256, "recheck_pending": True})["days"] is None
    assert ops._estimate({"MERGED": 10}, 8000, {"regions": 0, "recheck_pending": False}) == {
        "remainingCalls": 0,
        "days": 0,
        "basis": "남은 파티션 수 ÷ 하루 백필 몫 8,000회 = 일일 호출 상한 8,000 − 증분 몫 0(시군구 0 × 3개월)"
        " (같은 날 증분·재확인이 백필보다 먼저 호출하거나 다른 프로그램과 키를 공유하면 더 걸림)",
    }


class _StatusFake:
    """status() 용 가짜 오케스트레이터·운영 저장소 — 재확인 대기 여부만 원천마다 다르다 (상한·남은 양은 같게)."""

    def __init__(self, pending_source: str):
        self.pending_source = pending_source

    async def runs(self, statuses=None, created_after=None, limit=50):
        return []

    async def instigators(self):
        return {"schedules": [], "sensors": []}

    async def budget_day(self, day, source="rtms"):
        return {"limit_calls": 10000, "used_calls": 0, "by_priority": {}, "exhausted_reason": None,
                "exhausted_at": None}  # fmt: skip

    async def partition_counts(self, source="rtms"):
        return {"PENDING": 7000, "RETRY": 232, "MERGED": 100}

    async def budget_reserves(self, source="rtms"):
        return {"regions": 256, "recheck_pending": source == self.pending_source}

    async def month_counts(self, source="rtms"):
        return None

    async def last_version(self):
        return None


@pytest.mark.parametrize("pending_source", ["rtms", "rent"])
async def test_status_backfill_estimate_uses_each_sources_recheck_reserve(pending_source):
    """원천마다 자기 재확인 대기 값으로 백필 몫을 센다 — 매매만 재확인 대기인 날 전월세 예상이
    매매 몫(4,160)으로 계산되면(인자 누락·원천 뒤바뀜) 여기서 걸린다."""
    import datetime as dt

    fake = _StatusFake(pending_source)
    body, _ = await ops.status(
        fake,
        fake,
        today=dt.date(2026, 10, 21),
        budget_pct=80,
        rent_budget_pct=80,
        now=dt.datetime(2026, 10, 21, tzinfo=dt.UTC),
    )
    s = body["summary"]
    for est, src in ((s["backfillEstimate"], "rtms"), (s["rent"]["backfillEstimate"], "rent")):
        pending = src == pending_source
        share = 8000 - 768 - (3072 if pending else 0)  # 상한 8,000 − 증분 몫 768 [− 재확인 몫 3,072]
        assert est["remainingCalls"] == 7232
        assert est["days"] == (2 if pending else 1)  # 7,232 ÷ 4,160 → 2일, ÷ 7,232 → 1일
        assert f"하루 백필 몫 {share:,}회" in est["basis"]
        assert ("재확인 몫 3,072" in est["basis"]) is pending
