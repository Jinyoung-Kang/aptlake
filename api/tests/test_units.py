"""컨테이너 없이 도는 단위 테스트: 키 형식·HMAC, 커서 서명, 신뢰 프록시 IP 판별, 라우트 스코프 강제."""

import pytest
from fastapi import FastAPI
from hypothesis import given
from hypothesis import strategies as st
from starlette.requests import Request

from aptlake_api.core import cursor, keys
from aptlake_api.core.auth import client_ip
from aptlake_api.core.http import assert_all_routes_scoped, require_scope
from aptlake_api.core.settings import Settings

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
