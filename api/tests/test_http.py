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
