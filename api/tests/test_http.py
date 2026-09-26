"""API 계약·보안 테스트 (FR-501~504, 기획서 9장). 실제 PostgreSQL·Redis·ClickHouse 컨테이너 사용."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_asyncio

from aptlake_api import keys
from aptlake_api.settings import settings


@pytest_asyncio.fixture(scope="session")
async def apps(stack):
    settings.cache_clear()
    from aptlake_api.main import create_internal_app, create_public_app

    public, internal = create_public_app(), create_internal_app()
    async with public.router.lifespan_context(public), internal.router.lifespan_context(internal):
        await public.state.res.redis.set("al:ds:ver", "gold@test.1")
        yield public, internal


@pytest_asyncio.fixture(scope="session")
async def pub(apps):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps[0], client=("10.9.0.1", 1)), base_url="http://t"
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def adm(apps):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1]), base_url="http://t") as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def admin_key(stack):
    import psycopg

    issued = keys.issue("test-pepper")
    with psycopg.connect(stack["su"], autocommit=True) as c:
        cid = c.execute("INSERT INTO api.client (name, plan_id) VALUES ('op','pro') RETURNING client_id").fetchone()[0]
        c.execute(
            "INSERT INTO api.api_key VALUES (%s,%s,%s,%s, now(), now() + interval '1 day', NULL, NULL)",
            (issued.key_id, cid, issued.secret_hmac, ["admin", "read"]),
        )
    return issued.api_key


async def new_key(adm, admin_key, plan="free", scopes=("read",)) -> tuple[str, str]:
    r = await adm.post(
        "/v1/admin/clients", json={"name": f"c-{plan}", "planId": plan}, headers={"X-API-Key": admin_key}
    )
    assert r.status_code == 201
    cid = r.json()["clientId"]
    r = await adm.post(f"/v1/admin/clients/{cid}/keys", json={"scopes": list(scopes)}, headers={"X-API-Key": admin_key})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    return r.json()["apiKey"], cid


async def test_anonymous_months_contract(pub):
    r = await pub.get("/v1/regions/41135/months", params={"from": "2024-01", "to": "2024-12"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 12 and body["items"][0]["unit"] == "만원/㎡"
    # FR-503: 헤더와 본문의 데이터셋 버전 일치
    assert r.headers["x-dataset-version"] == body["datasetVersion"] == "gold@test.1"
    for h in ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "etag", "x-trace-id"):
        assert h in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"
    # ETag 재검증
    r2 = await pub.get(
        "/v1/regions/41135/months",
        params={"from": "2024-01", "to": "2024-12"},
        headers={"If-None-Match": r.headers["etag"]},
    )
    assert r2.status_code == 304


async def test_plan_range_limits(pub, adm, admin_key):
    r = await pub.get("/v1/regions/41135/months", params={"from": "2023-01", "to": "2024-12"})
    assert r.status_code == 422 and r.json()["code"] == "RANGE_EXCEEDS_PLAN"  # anonymous 12개월
    key, _ = await new_key(adm, admin_key, "free")
    r = await pub.get(
        "/v1/regions/41135/months", params={"from": "2023-01", "to": "2024-12"}, headers={"X-API-Key": key}
    )
    assert r.status_code == 200  # free 24개월


async def test_revocation_is_immediate(pub, adm, admin_key):
    key, _ = await new_key(adm, admin_key)
    h = {"X-API-Key": key}
    assert (await pub.get("/v1/regions", headers=h)).status_code == 200  # 키 캐시가 채워진 상태
    key_id = keys.parse(key)[0]
    assert (await adm.delete(f"/v1/admin/keys/{key_id}", headers={"X-API-Key": admin_key})).status_code == 200
    r = await pub.get("/v1/regions", headers=h)
    assert r.status_code == 401 and r.json()["code"] == "KEY_REVOKED"


async def test_bad_keys_and_scopes(pub, adm, admin_key):
    assert (await pub.get("/v1/regions", headers={"X-API-Key": "nope"})).json()["code"] == "INVALID_API_KEY"
    key, _ = await new_key(adm, admin_key)
    kid = keys.parse(key)[0]
    wrong = f"al_live_{kid}." + "A" * 43
    assert (await pub.get("/v1/regions", headers={"X-API-Key": wrong})).status_code == 401
    r = await pub.post("/v1/exports", json={"from": "2024-07-01", "to": "2024-07-31"}, headers={"X-API-Key": key})
    assert r.status_code == 403 and r.json()["code"] == "SCOPE_REQUIRED"
    r = await adm.post("/v1/admin/clients", json={"name": "x"}, headers={"X-API-Key": key})
    assert r.status_code == 403


async def test_admin_routes_absent_on_public_app(apps):
    from aptlake_api.deps import iter_api_routes

    public, internal = apps
    pub_paths = {r.path for r in iter_api_routes(public.routes)}
    int_paths = {r.path for r in iter_api_routes(internal.routes)}
    assert "/v1/trades" in pub_paths and not any(p.startswith("/v1/admin") for p in pub_paths)
    assert "/v1/admin/keys/{keyId}" in int_paths


async def test_cursor_pagination_is_complete_and_signed(pub, adm, admin_key):
    key, _ = await new_key(adm, admin_key)
    h = {"X-API-Key": key}
    q = {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 100, "includeCancelled": "true"}
    seen, cursor = [], None
    while True:
        r = await pub.get("/v1/trades", params={**q, **({"cursor": cursor} if cursor else {})}, headers=h)
        assert r.status_code == 200
        seen += [t["tradeId"] for t in r.json()["items"]]
        cursor = r.json()["page"]["nextCursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 250
    first = (await pub.get("/v1/trades", params=q, headers=h)).json()["page"]["nextCursor"]
    r = await pub.get("/v1/trades", params={**q, "to": "2024-07-30", "cursor": first}, headers=h)
    assert r.status_code == 400 and r.json()["code"] == "INVALID_CURSOR"  # 다른 질의에 재사용
    r = await pub.get("/v1/trades", params={**q, "cursor": first[:-2] + "xx"}, headers=h)
    assert r.status_code == 400  # 서명 변조


@pytest.mark.parametrize(
    "path",
    [
        "/v1/regions/11110' OR '1'='1/months?from=2024-01&to=2024-02",
        "/v1/trades?sggCd=41135;DROP TABLE trade_current&from=2024-07-01&to=2024-07-31",
        "/v1/trades/1' UNION SELECT 1--/history",
        "/v1/complexes/c_aaaa' OR 1=1--",
        "/v1/index?regionId=00 OR 1=1",
    ],
)
async def test_injection_strings_are_rejected_not_executed(pub, path):
    r = await pub.get(path)
    assert r.status_code in (400, 404, 422, 429)
    assert r.headers["content-type"].startswith("application/problem+json")


async def test_history_diff(pub):
    r = await pub.get("/v1/trades/41135-202407-0000000000000000-0/history")
    v = r.json()["versions"]
    assert [x["version"] for x in v] == [1, 2]
    assert {"field": "cancelled", "from": False, "to": True} in v[1]["changes"]


async def test_usage_is_metered_and_scoped_to_client(pub, adm, admin_key, apps):
    k1, c1 = await new_key(adm, admin_key)
    k2, _ = await new_key(adm, admin_key)
    for _ in range(3):
        await pub.get("/v1/regions", headers={"X-API-Key": k1})
    await pub.get("/v1/regions", headers={"X-API-Key": k2})
    await asyncio.sleep(0.8)  # 배치 flush (0.2s 주기)
    r = await pub.get("/v1/me/usage", headers={"X-API-Key": k1})
    assert r.status_code == 200 and r.json()["clientId"] == c1
    assert sum(i["requests"] for i in r.json()["items"]) == 3  # 다른 클라이언트 요청은 보이지 않음


async def test_rate_limit_returns_429_with_retry_after(apps):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps[0], client=("10.9.9.9", 1)), base_url="http://t"
    ) as c:
        codes = [(await c.get("/v1/regions")).status_code for _ in range(25)]
        assert codes[:20] == [200] * 20 and codes[-1] == 429
        r = await c.get("/v1/regions")
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert r.headers["x-ratelimit-remaining"] == "0"


# ───────────── 웹 BFF 플랜 · 상태 응답 ETag · 시장 요약 · 수집 상태 ─────────────


def client_at(app, ip: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(ip, 1)), base_url="http://t")


async def test_web_plan_limits_per_browser_ip(apps, stack):
    import psycopg

    issued = keys.issue("test-pepper")
    with psycopg.connect(stack["su"], autocommit=True) as c:
        cid = c.execute(
            "INSERT INTO api.client (name, plan_id) VALUES ('web-ui','web') RETURNING client_id"
        ).fetchone()[0]
        c.execute(
            "INSERT INTO api.api_key VALUES (%s,%s,%s,%s, now(), now() + interval '1 day', NULL, NULL)",
            (issued.key_id, cid, issued.secret_hmac, ["read"]),
        )
    h = {"X-API-Key": issued.api_key}

    async def remaining(ip: str) -> int:
        async with client_at(apps[0], ip) as c:
            r = await c.get("/v1/regions", headers=h)
            assert r.status_code == 200 and r.headers["x-ratelimit-limit"] == "300"
            return int(r.headers["x-ratelimit-remaining"])

    a1, a2, b1 = await remaining("10.20.0.1"), await remaining("10.20.0.1"), await remaining("10.20.0.2")
    # 서버 쪽 키 하나를 모든 브라우저가 나눠 쓰지만, 한도는 브라우저 IP 마다 따로 센다
    assert a2 == a1 - 1 and b1 == a1


async def test_status_etag_follows_content(apps, stack):
    import psycopg

    async with client_at(apps[0], "10.30.0.1") as c:
        r1 = await c.get("/v1/quality/summary")
        assert r1.status_code == 200 and r1.headers["cache-control"] == "private, no-cache"
        assert (await c.get("/v1/quality/summary", headers={"If-None-Match": r1.headers["etag"]})).status_code == 304
        with psycopg.connect(stack["su"], autocommit=True) as pg:
            pg.execute("INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status) VALUES ('11110','202401','RETRY')")
        # 요청이 같아도 내용이 바뀌었으면 304 가 아니라 새 본문
        r3 = await c.get("/v1/quality/summary", headers={"If-None-Match": r1.headers["etag"]})
        assert r3.status_code == 200 and r3.headers["etag"] != r1.headers["etag"]
        assert r3.json()["partitions"]["RETRY"] >= 1


async def test_ticker_and_index_use_confirmed_months(apps):
    async with client_at(apps[0], "10.30.0.2") as c:
        t = (await c.get("/v1/market/ticker")).json()
        items = {i["key"]: i for i in t["items"]}
        assert items["median"]["unit"] == "만원/㎡"
        assert items["trades"]["label"] == "전국 거래 2024-12" and items["trades"]["value"] == 1070
        assert items["trades"]["change"] == round((1070 / 1060 - 1) * 100, 2)
        assert items["index_00"]["label"] == "전국 지수 2024-12" and items["index_00"]["provisional"] is False
        assert items["index_00"]["change"] == round((123 / 122 - 1) * 100, 2)
        assert t["available"] == {"from": "2024-01", "to": "2024-12", "default": "2024-12"}

        [s] = (await c.get("/v1/index/summary")).json()["items"]
        assert s["period"] == s["confirmed"]["period"] == "2024-12"
        assert s["confirmed"]["yoy"] == round((123 / 111 - 1) * 100, 2)

        o = (await c.get("/v1/market/overview", params={"ym": "2024-12"})).json()
        assert o["nation"]["trades"] == 1070 and [x["regionId"] for x in o["sido"]] == ["41"]
        assert o["rankings"]["volume"][0]["sggCd"] == "41135"
        r = await c.get("/v1/market/overview", params={"ym": "2025-06"})
        assert r.status_code == 422 and r.json()["code"] == "MONTH_OUT_OF_RANGE"


def fake_dagster(now: float):
    """Dagster GraphQL 가짜 응답: 이후 성공으로 복구된 실패 1건, 아직 실패 중 1건, 대기·실행 중 작업, 오류 난 센서 틱."""
    import json

    from aptlake_api import ops

    def run(rid, status, created, partition="202407", ended=True):
        return {
            "runId": rid,
            "jobName": "month_pipeline",
            "status": status,
            "creationTime": created,
            "startTime": created + 5 if status != "QUEUED" else None,
            "endTime": created + 60 if ended else None,
            "tags": [{"key": "dagster/partition", "value": partition}, {"key": "aptlake/priority", "value": "retry"}],
        }

    failed = [run("fail-old", "FAILURE", now - 7200), run("fail-new", "FAILURE", now - 600, "202408")]
    succeeded = [run("ok-1", "SUCCESS", now - 3600)]
    active = [run("q-1", "QUEUED", now - 30, "202409", False), run("r-1", "STARTED", now - 90, "202410", False)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q, v = body["query"], body.get("variables") or {}
        if "logsForRun" in q:
            data = {
                "logsForRun": {
                    "__typename": "EventConnection",
                    "events": [
                        {
                            "__typename": "ExecutionStepFailureEvent",
                            "timestamp": str(int((now - 550) * 1000)),  # 이벤트 시각은 밀리초
                            "stepKey": "bronze__rtms",
                            "error": {
                                "className": "HTTPError",
                                "message": f"502 for https://apis.data.go.kr/x?serviceKey=LEAKME ({v['id']})",
                                "stack": ["  File a.py\n"],
                                "causes": [],
                            },
                        }
                    ],
                }
            }
        elif "repositoriesOrError" in q:
            sensor = {
                "name": "due_partitions_sensor",
                "minIntervalSeconds": 300,
                "nextTick": {"timestamp": now + 60},
                "sensorState": {
                    "status": "RUNNING",
                    "ticks": [
                        {
                            "status": "FAILURE",
                            "timestamp": now - 100,
                            "skipReason": None,
                            "runIds": [],
                            "error": {"message": "boom postgresql://pipeline:hunter2@postgres/aptlake"},
                        }
                    ],
                },
            }
            data = {"repositoriesOrError": {"nodes": [{"schedules": [], "sensors": [sensor]}]}}
        else:
            st = (v.get("f") or {}).get("statuses")
            rows = {"FAILURE": failed, "SUCCESS": succeeded}.get(st[0] if st and len(st) == 1 else "")
            if rows is None:
                rows = active if st else failed + succeeded
            data = {"runsOrError": {"__typename": "Runs", "results": rows}}
        return httpx.Response(200, json={"data": data})

    return ops.DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_ops_status_and_error_log(apps):
    import json
    import time

    public = apps[0]
    real = public.state.dagster
    public.state.dagster = fake_dagster(time.time())
    try:
        async with client_at(public, "10.40.0.1") as c:
            s = (await c.get("/v1/ops/status")).json()
            assert s["pipeline"]["available"] is True
            assert {j["runId"]: j["status"] for j in s["jobs"]["active"]} == {"q-1": "QUEUED", "r-1": "STARTED"}
            [sensor] = s["schedules"]
            assert sensor["lastTick"]["status"] == "FAILURE" and "hunter2" not in json.dumps(s)

            e = (await c.get("/v1/ops/errors", params={"hours": 24})).json()
            pipe = [x for x in e["entries"] if x["source"] == "pipeline"]
            # 같은 작업·파티션이 나중에 성공한 실패(fail-old)는 기본으로 숨긴다
            assert {x["ref"] for x in pipe if x["ref"]} == {"fail-new"}
            [run_err] = [x for x in pipe if x["ref"] == "fail-new"]
            assert run_err["where"] == "월 수집·반영·발행 · 202408 · bronze__rtms"
            assert run_err["at"].startswith(time.strftime("%Y-%m-%d", time.gmtime(time.time() - 550)))
            assert any(x["id"].startswith("tick:") for x in pipe)
            assert "LEAKME" not in json.dumps(e) and "hunter2" not in json.dumps(e)

            e2 = (await c.get("/v1/ops/errors", params={"hours": 24, "includeResolved": "true"})).json()
            flags = {x["ref"]: x["resolved"] for x in e2["entries"] if x["source"] == "pipeline" and x["ref"]}
            assert flags == {"fail-old": True, "fail-new": False}
    finally:
        await public.state.dagster.aclose()
        public.state.dagster = real


async def test_ops_status_survives_dagster_down(apps):
    from aptlake_api import ops

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    public = apps[0]
    real = public.state.dagster
    public.state.dagster = ops.DagsterClient(
        "http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    try:
        async with client_at(public, "10.40.0.2") as c:
            r = await c.get("/v1/ops/status")
            assert r.status_code == 200 and r.json()["pipeline"]["available"] is False
            assert r.json()["summary"]["totalPartitions"] >= 0  # 운영 DB 부분은 그대로
            r = await c.get("/v1/ops/errors", params={"hours": 24})
            assert r.status_code == 200 and r.json()["notes"]
    finally:
        await public.state.dagster.aclose()
        public.state.dagster = real


async def test_quality_grid_range_depends_on_scope(apps, stack):
    import psycopg

    with psycopg.connect(stack["su"], autocommit=True) as pg:
        pg.execute("INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status) VALUES ('11110','202402','MERGED')")
    async with client_at(apps[0], "10.30.0.3") as c:
        q = {"from": "2020-10", "to": "2026-09"}  # 72개월 (품질 화면의 기본 범위)
        r = await c.get("/v1/quality/partitions", params=q)
        assert r.status_code == 422 and r.json()["detail"] == "최대 60개월"  # 전국 격자는 칸 수 제한
        r = await c.get("/v1/quality/partitions", params={**q, "sido": "11"})
        assert r.status_code == 200 and r.json()["cells"]["11110"]["202402"][0] == "MERGED"
