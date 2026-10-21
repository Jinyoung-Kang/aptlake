"""컨테이너 없이 도는 단위 테스트: 키 형식·HMAC, 커서 서명, 신뢰 프록시 IP 판별, 라우트 스코프 강제, 원천별 데이터 기준 시각."""

import asyncio
import datetime as dt
from types import SimpleNamespace

import orjson
import pytest
from fastapi import FastAPI
from hypothesis import given
from hypothesis import strategies as st
from psycopg_pool import PoolTimeout
from starlette.requests import Request

from aptlake_api.core import cursor, keys
from aptlake_api.core.auth import Plan, Principal, client_ip
from aptlake_api.core.http import (
    RECORDED_AS_OF_RETRY_S,
    RECORDED_AS_OF_WAIT_S,
    DatasetVersion,
    assert_all_routes_scoped,
    data_as_of,
    require_scope,
    respond,
)
from aptlake_api.core.settings import Settings
from aptlake_api.core.singleflight import SingleFlight

PEPPER = "pepper-for-tests"


def test_issue_parse_verify_roundtrip():
    k = keys.issue(PEPPER)
    assert k.api_key.startswith("al_live_") and len(k.key_id) == 12
    key_id, secret = keys.parse(k.api_key)
    assert key_id == k.key_id
    assert keys.verify(PEPPER, secret, k.secret_hmac)
    assert not keys.verify("other-pepper", secret, k.secret_hmac)
    assert secret not in k.secret_hmac  # 저장값에 원문이 없다


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "al_live_",
        "al_live_ABC.def",
        "al_test_" + "A" * 12 + "." + "a" * 43,
        "al_live_" + "0" * 12 + "." + "a" * 43,  # 혼동 문자(0) 불가
        "al_live_" + "A" * 12 + "." + "a" * 43 + "' OR 1=1",
    ],
)
def test_parse_rejects_malformed(raw):
    assert keys.parse(raw) is None


@given(st.dictionaries(st.sampled_from(["d", "k"]), st.text(max_size=20)))
def test_cursor_roundtrip_and_query_binding(pos):
    key = b"k" * 32
    fp = cursor.query_fingerprint({"sgg": "11110", "a": "2024-01-01"})
    tok = cursor.encode(key, pos, fp)
    assert cursor.decode(key, tok, fp) == pos
    assert cursor.decode(key, tok, cursor.query_fingerprint({"sgg": "11140"})) is None  # 다른 질의
    assert cursor.decode(b"x" * 32, tok, fp) is None  # 다른 키


def test_cursor_tamper_rejected():
    key = b"k" * 32
    tok = cursor.encode(key, {"d": "2024-07-31", "k": "x"}, "fp")
    body, sig = tok.split(".")
    forged = cursor.encode(b"attacker" * 4, {"d": "2099-01-01", "k": "x"}, "fp").split(".")[0] + "." + sig
    assert cursor.decode(key, forged, "fp") is None
    for bad in ["", "abc", "a.b.c", "%%%.%%%", tok + "x"]:
        assert cursor.decode(key, bad, "fp") is None


def _req(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def _settings(trusted: str) -> Settings:
    return Settings(
        pg_dsn="x",
        redis_url="x",
        ch_reader_password="x",
        ch_usage_password="x",
        api_key_pepper="x",
        cursor_signing_key="x",
        trusted_proxies=trusted,
    )


def test_client_ip_ignores_xff_from_untrusted_peer():
    s = _settings("172.30.61.250/32")
    assert client_ip(_req("10.0.0.5", "1.2.3.4"), s) == "10.0.0.5"


def test_client_ip_uses_rightmost_untrusted_hop_from_trusted_proxy():
    s = _settings("172.30.61.250/32")
    # 클라이언트가 앞에 위조 값을 붙여도, 프록시가 붙인 마지막 주소를 쓴다
    assert client_ip(_req("172.30.61.250", "6.6.6.6, 192.168.65.1"), s) == "192.168.65.1"


def test_unscoped_route_fails_startup():
    app = FastAPI()

    @app.get("/v1/ok")
    async def ok(p=require_scope("read")):
        return {}

    assert_all_routes_scoped(app)

    @app.get("/v1/leak")
    async def leak():
        return {}

    with pytest.raises(RuntimeError, match="/v1/leak"):
        assert_all_routes_scoped(app)


def test_unscoped_route_inside_included_router_fails_startup():
    from fastapi import APIRouter

    router = APIRouter(prefix="/v1/x")

    @router.get("/hidden")
    async def hidden():
        return {}

    app = FastAPI()
    app.include_router(router)
    with pytest.raises(RuntimeError, match="/v1/x/hidden"):
        assert_all_routes_scoped(app)


async def test_singleflight_first_caller_cancelled_others_still_get_result():
    sf, calls = SingleFlight(), 0

    async def work():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return "v"

    first = asyncio.create_task(sf.do("k", work))
    await asyncio.sleep(0)
    second = asyncio.create_task(sf.do("k", work))
    first.cancel()  # 처음 요청한 쪽의 연결이 끊겨도
    assert await second == ("v", False)  # 기다리던 쪽은 결과를 받는다
    assert calls == 1 and len(sf) == 0


async def test_singleflight_error_reaches_all_waiters_and_key_is_released():
    sf, calls = SingleFlight(), 0

    async def boom():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise ValueError("x")

    rs = await asyncio.gather(*(sf.do("k", boom) for _ in range(5)), return_exceptions=True)
    assert calls == 1 and all(isinstance(r, ValueError) for r in rs)
    assert len(sf) == 0  # 다음 요청은 다시 계산한다
    await asyncio.gather(sf.do("k", boom), return_exceptions=True)
    assert calls == 2


T = "2026-10-21T08:00:00+00:00"
R = "2026-10-21T06:30:00+00:00"


@pytest.mark.parametrize(
    "asof,source,expected",
    [
        ({"trade": T, "rent": R}, "trade", T),
        ({"trade": T, "rent": R}, "rent", R),
        ({"trade": T, "rent": R}, "all", R),  # 두 원천 중 이른 쪽
        ({"trade": "2026-10-21T15:00:00+09:00", "rent": R}, "all", "2026-10-21T15:00:00+09:00"),  # 시각 비교
        ({"trade": T, "rent": ""}, "all", ""),  # 한쪽을 모르면 모른다
        ({"trade": T, "rent": ""}, "rent", ""),
        ({"trade": T, "rent": "not-a-time"}, "all", ""),
        ({"trade": T, "rent": "2026-10-21T06:30:00"}, "all", ""),  # 시간대 없는 값과는 비교하지 않는다
        ({}, "trade", ""),
    ],
)
def test_data_as_of_per_source(asof, source, expected):
    assert data_as_of(asof, source) == expected


class FakeRedis:
    def __init__(self, values):
        self.values, self.calls = values, []

    async def mget(self, *keys):
        self.calls.append(keys)
        return [self.values.get(k) for k in keys]

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value.decode() if isinstance(value, bytes) else value


class FakePg:
    """발행 기록 조회 (원천별 max(data_as_of)) — 불린 횟수와 연결을 기다린 상한을 센다."""

    def __init__(self, rows=None, fail=False, delay=0.0, hang=None):
        self.rows, self.fail, self.delay, self.queries, self.waits = rows or [], fail, delay, 0, []
        self.hang: asyncio.Event | None = hang  # 주어지면 연결은 바로 내주고 질의는 이 이벤트까지 끝나지 않는다

    def connection(self, timeout=None):
        self.waits.append(timeout)
        pg = self

        class Conn:
            async def __aenter__(self):
                await asyncio.sleep(pg.delay)
                if pg.fail:  # 운영 DB 장애 — 풀이 연결을 내주지 못한다 (psycopg.OperationalError 의 하위)
                    raise PoolTimeout("couldn't get a connection")
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql):
                pg.queries += 1
                if pg.hang is not None:  # 서버에 닿지 않는 질의 — statement_timeout 도 걸리지 않는다
                    await pg.hang.wait()

                class Cur:
                    async def fetchall(self):
                        return pg.rows

                return Cur()

        return Conn()


def _request(redis, pg=None):
    app = type("App", (), {})()
    app.state = type("State", (), {"res": type("Res", (), {"redis": redis, "pg": pg or FakePg()})()})()
    return type("Req", (), {"app": app})()


async def test_dataset_version_reads_version_and_both_as_of_keys_in_one_mget():
    redis = FakeRedis({"al:ds:ver": "gold@2026-10-21.4", "al:ds:asof:trade": T})
    pg = FakePg()
    request = _request(redis, pg)
    dsv = DatasetVersion()
    assert await dsv.get(request) == ("gold@2026-10-21.4", {"trade": T, "rent": ""}, False)
    assert await dsv.get(request) == ("gold@2026-10-21.4", {"trade": T, "rent": ""}, False)  # 1초 캐시
    assert redis.calls == [("al:ds:ver", "al:ds:asof", "al:ds:asof:trade", "al:ds:asof:rent")]
    # 옛 전역 키가 없으면(원천별 키를 쓰는 파이프라인이 발행했다) 빠진 원천은 모른다 — 발행 기록을 읽지 않는다
    assert pg.queries == 0


async def test_dataset_version_fills_missing_sources_from_publish_records_while_legacy_key_remains(monkeypatch):
    """전환 구간: 옛 파이프라인(전역 키만 쓴다)이 발행한 상태 — 옛 키 값은 쓰지 않고 빠진 원천만 발행 기록에서, 버전마다 한 번."""
    clock = [100.0]
    monkeypatch.setattr("aptlake_api.core.http.time.monotonic", lambda: clock[0])
    redis = FakeRedis({"al:ds:ver": "gold@2026-10-21.33", "al:ds:asof": "2030-01-01T00:00:00+00:00"})
    kst = dt.timezone(dt.timedelta(hours=9))
    pg = FakePg([{"source": "trade", "t": dt.datetime(2026, 10, 21, 17, 6, 31, 33482, tzinfo=kst)},
                 {"source": "rent", "t": dt.datetime(2026, 10, 21, 9, 17, 32, 615430, tzinfo=dt.UTC)}])  # fmt: skip
    dsv = DatasetVersion()
    want = {"trade": "2026-10-21T08:06:31.033482+00:00", "rent": "2026-10-21T09:17:32.615430+00:00"}  # UTC 로
    assert await dsv.get(_request(redis, pg)) == ("gold@2026-10-21.33", want, False)
    assert pg.waits == [RECORDED_AS_OF_WAIT_S]  # 풀 기본 대기(30초)가 아니라 짧게
    clock[0] += 2
    assert await dsv.get(_request(redis, pg)) == ("gold@2026-10-21.33", want, False) and pg.queries == 1  # 같은 버전
    redis.values["al:ds:asof:trade"] = T  # 키가 있는 원천은 키 값 그대로
    redis.values["al:ds:ver"] = "gold@2026-10-21.34"
    clock[0] += 2
    assert await dsv.get(_request(redis, pg)) == ("gold@2026-10-21.34", {**want, "trade": T}, False) and pg.queries == 2
    # 운영 DB 를 못 읽으면 모르는 값이고 불완전(degraded) — 실패는 기억하지 않지만 잠시 다시 읽지 않는다
    redis.values["al:ds:ver"] = "gold@2026-10-21.35"
    clock[0] += 2
    down = FakePg(fail=True)
    assert await dsv.get(_request(redis, down)) == ("gold@2026-10-21.35", {"trade": T, "rent": ""}, True)
    clock[0] += 2
    assert await dsv.get(_request(redis, down)) == ("gold@2026-10-21.35", {"trade": T, "rent": ""}, True)
    assert len(down.waits) == 1  # 장애 동안 1초 갱신마다 연결을 기다리지 않는다
    clock[0] += RECORDED_AS_OF_RETRY_S
    assert await dsv.get(_request(redis, pg)) == ("gold@2026-10-21.35", {**want, "trade": T}, False) and pg.queries == 3
    # 원천별 키를 쓰는 파이프라인이 발행하면(옛 키를 지운다) 더는 읽지 않는다
    redis.values.pop("al:ds:asof")
    redis.values["al:ds:ver"] = "gold@2026-10-21.36"
    clock[0] += 2
    assert await dsv.get(_request(redis, pg)) == ("gold@2026-10-21.36", {"trade": T, "rent": ""}, False)
    assert pg.queries == 3


async def test_dataset_version_concurrent_refreshes_share_one_publish_record_read():
    """갱신하는 동안 몰린 요청은 발행 기록 조회 하나를 함께 기다린다 — 장애 때 저마다 연결을 기다리지 않게."""
    redis = FakeRedis({"al:ds:ver": "gold@2026-10-21.33", "al:ds:asof": "2030-01-01T00:00:00+00:00"})
    for pg, want in (
        (
            FakePg([{"source": "rent", "t": dt.datetime(2026, 10, 21, 6, 30, tzinfo=dt.UTC)}], delay=0.05),
            ({"rent": R}, False),
        ),
        (FakePg(fail=True, delay=0.05), ({}, True)),
    ):
        dsv = DatasetVersion()
        got = await asyncio.gather(*(dsv.get(_request(redis, pg)) for _ in range(5)))
        assert len(pg.waits) == 1
        assert got == [("gold@2026-10-21.33", {"trade": "", "rent": "", **want[0]}, want[1])] * 5


async def test_dataset_version_bounds_wait_when_publish_record_query_hangs(monkeypatch):
    """연결은 받았는데 질의가 끝나지 않으면(서버에 닿지 않아 statement_timeout 도 걸리지 않는다) 기다리던 요청은 상한에서
    불완전(degraded)으로 돌아온다 — 조회는 취소하지 않고 남겨 두며, 잠시 다시 기다리지 않고, 늦게라도 성공하면 그 값을 쓴다."""
    clock = [100.0]  # DatasetVersion 의 1초 캐시·재조회 대기만 가짜 시계로 (asyncio 의 시간 상한은 실제 시계)
    monkeypatch.setattr("aptlake_api.core.http.time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr("aptlake_api.core.http.RECORDED_AS_OF_DEADLINE_S", 0.05)
    ver = "gold@2026-10-21.33"
    redis = FakeRedis({"al:ds:ver": ver, "al:ds:asof": "2030-01-01T00:00:00+00:00", "al:ds:asof:trade": T})
    release = asyncio.Event()
    pg = FakePg([{"source": "rent", "t": dt.datetime(2026, 10, 21, 6, 30, tzinfo=dt.UTC)}], hang=release)
    dsv = DatasetVersion()
    loop = asyncio.get_running_loop()
    degraded = (ver, {"trade": T, "rent": ""}, True)

    started = loop.time()
    async with asyncio.timeout(2):  # 상한이 없으면 여기서 끝나지 않는다 — 멈추지 않고 실패하게
        got = await asyncio.gather(*(dsv.get(_request(redis, pg)) for _ in range(5)))
    assert got == [degraded] * 5 and loop.time() - started < 1.0  # 상한(0.05초)에서 함께 돌아온다
    assert len(pg.waits) == 1 and len(dsv._flight) == 1  # 조회 하나가 취소되지 않고 남아 있다
    # 재조회 대기 동안은 1초 갱신마다 기다리지 않는다
    clock[0] += 2
    started = loop.time()
    assert await dsv.get(_request(redis, pg)) == degraded and loop.time() - started < 0.04
    # 대기가 지나도 조회가 남아 있으면 새 연결을 청하지 않고 그 조회를 다시 상한까지만 기다린다
    clock[0] += RECORDED_AS_OF_RETRY_S
    async with asyncio.timeout(2):
        assert await dsv.get(_request(redis, pg)) == degraded and len(pg.waits) == 1
    # 늦게 끝난 조회의 값은 기억되어 다음 갱신부터 쓰인다 (재조회 대기 중이어도)
    release.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert len(dsv._flight) == 0
    clock[0] += 2
    assert await dsv.get(_request(redis, pg)) == (ver, {"trade": T, "rent": R}, False)
    assert len(pg.waits) == 1 and pg.queries == 1


def _respond_request(app, etag=None):
    plan = Plan("anonymous", rpm=60, daily_rows=10_000, max_range_months=None, max_page_size=100, allow_bulk=False)
    principal = Principal("anonymous", "ip:test", "anonymous", plan, frozenset({"read"}))
    return SimpleNamespace(
        app=app, headers={"if-none-match": etag} if etag else {}, state=SimpleNamespace(principal=principal)
    )


async def test_respond_keeps_body_without_as_of_out_of_cache_while_publish_records_unreadable(monkeypatch):
    """전환 대체 경로에서 운영 DB 를 못 읽은 순간의 본문(dataAsOf null)은 결과 캐시·버전 ETag 에 남기지 않는다 —
    남기면 복구 뒤에도 같은 버전에서 캐시·304 로 계속 나간다."""
    clock = [100.0]
    monkeypatch.setattr("aptlake_api.core.http.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("aptlake_api.core.http.settings", lambda: SimpleNamespace(result_cache_ttl_s=600))
    redis = FakeRedis(
        {"al:ds:ver": "gold@2026-10-21.33", "al:ds:asof": "2030-01-01T00:00:00+00:00", "al:ds:asof:trade": T}
    )
    pg = FakePg(fail=True)
    res = SimpleNamespace(redis=redis, pg=pg)
    app = SimpleNamespace(state=SimpleNamespace(res=res, dsv=DatasetVersion(), flight=SingleFlight()))

    async def compute():
        return {"items": []}, 0

    async def call(route, source, etag=None):
        return await respond(_respond_request(app, etag), route, {"sgg": "41135"}, compute, source=source)

    def cached():
        return [k for k in redis.values if k.startswith("al:cache:")]

    down = await call("rent_months", "rent")
    assert down.status_code == 200 and orjson.loads(down.body)["dataAsOf"] is None
    assert down.headers["x-data-as-of"] == ""
    assert (down.headers["x-cache"], down.headers["cache-control"]) == ("bypass", "private, no-cache")
    assert cached() == []
    # 원천별 키가 있는 원천의 응답은 복구 뒤에도 같은 본문이라 그대로 캐시한다
    trade = await call("region_months", "trade")
    assert trade.headers["x-cache"] == "miss" and orjson.loads(trade.body)["dataAsOf"] == T and len(cached()) == 1
    # 복구: 같은 버전에서 기준 시각이 실린 본문을 새로 계산해 새 ETag 로 보낸다 (장애 때 받은 ETag 로 304 가 아니다)
    pg.fail, pg.rows = False, [{"source": "rent", "t": dt.datetime(2026, 10, 21, 6, 30, tzinfo=dt.UTC)}]
    clock[0] += RECORDED_AS_OF_RETRY_S + 1
    up = await call("rent_months", "rent", etag=down.headers["etag"])
    assert up.status_code == 200 and up.headers["x-cache"] == "miss" and up.headers["etag"] != down.headers["etag"]
    assert orjson.loads(up.body)["dataAsOf"] == up.headers["x-data-as-of"] == R and len(cached()) == 2
