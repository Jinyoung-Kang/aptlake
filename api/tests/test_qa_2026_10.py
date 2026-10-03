"""2026-10 출시 기준 QA 에서 찾은 결함의 재현 시험. 번호는 docs/qa/2026-10-qa-report.md 의 결함 번호와 같다."""

from __future__ import annotations

import asyncio
import time

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


# QA-007: 서빙 DB(ClickHouse)가 응답하지 않으면 API 요청이 시간 초과 없이 매달렸다 (클라이언트 기본 300초).
#         QA 스택에서 15초 멈추자 요청이 15.9초까지 기다렸다 — Redis 는 2초 만에 503 으로 끝난다.
async def test_qa_007_unresponsive_clickhouse_fails_fast_with_503(pub, stack):
    container = stack["ch_container"].get_wrapped_container()
    container.pause()
    try:
        t = time.perf_counter()
        try:
            r = await asyncio.wait_for(pub.get("/v1/search", params={"q": "응답없음"}), timeout=25)
        except TimeoutError:
            pytest.fail("ClickHouse 가 멈춘 동안 25초가 지나도 응답이 없음 (시간 초과 없음)")
        elapsed = time.perf_counter() - t
    finally:
        container.unpause()
    assert r.status_code == 503, (r.status_code, r.text[:200])
    assert elapsed < 15, elapsed


# QA-008: 운영 DB(PostgreSQL) 질의에 시간·잠금 상한이 없어, 긴 트랜잭션이 잠금을 쥐면 그동안 무기한 기다렸다
#         (QA 스택: ops.log_view 를 20초 잠그자 /v1/ops/errors 가 18.3초 뒤 200). 연결 풀(10)이 고갈되면 다른 경로도 멈춘다.
async def test_qa_008_postgres_lock_wait_is_bounded(pub, adm, admin_key, stack):
    import psycopg

    key, _ = await new_key(adm, admin_key, "pro", scopes=("ops",))
    with psycopg.connect(stack["su"]) as c:
        c.execute("LOCK TABLE ops.log_view IN ACCESS EXCLUSIVE MODE")  # 트랜잭션 안 — 끝날 때까지 잠금 유지
        t = time.perf_counter()
        try:
            r = await asyncio.wait_for(pub.get("/v1/ops/errors", headers={"X-API-Key": key}), timeout=25)
        except TimeoutError:
            pytest.fail("잠금이 풀리지 않는 동안 25초가 지나도 응답이 없음 (시간·잠금 상한 없음)")
        finally:
            c.rollback()
        elapsed = time.perf_counter() - t
    assert r.status_code == 503, (r.status_code, r.text[:200])
    assert elapsed < 10, elapsed


# QA-004: /docs 가 버전을 고정하지 않은 외부 스크립트(swagger-ui-dist@5)를 무결성 검사(SRI) 없이 불렀다. 같은 출처라
#         웹 BFF 가 붙이는 웹 키(read·ops) 권한으로 API 를 부를 수 있어, CDN·패키지가 오염되면 그 권한이 넘어간다.
async def test_qa_004_docs_external_assets_are_pinned_with_sri(pub):
    import re

    r = await pub.get("/docs")
    assert r.status_code == 200
    tags = re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"https?://[^\"]+\"[^>]*>", r.text)
    assets = [t for t in tags if "swagger-ui" in t]
    assert len(assets) >= 2, tags  # 스크립트·스타일시트
    for t in assets:
        assert re.search(r"swagger-ui-dist@\d+\.\d+\.\d+/", t), f"버전 고정 아님: {t}"
        assert re.search(r'integrity="sha(256|384|512)-[A-Za-z0-9+/=]+"', t), f"SRI 없음: {t}"
        assert 'crossorigin="anonymous"' in t, t


# QA-001: 웹 BFF 키가 ops 전체 권한이라, 공개 웹에서 누구나(브라우저 밖에서도 Sec-Fetch-Site 헤더 한 줄로)
#         내부 오류 로그를 비우고 되돌릴 수 있었다. 결정(사용자, 2026-10-04): 보기는 공개(웹 키 = read + ops_read),
#         비우기·되돌리기는 ops 권한(운영자 키)만.
async def test_qa_001_ops_read_scope_views_but_cannot_clear(pub, adm, admin_key):
    viewer, _ = await new_key(adm, admin_key, "free", scopes=("read", "ops_read"))
    for path in ("/v1/ops/status", "/v1/ops/errors", "/v1/ops/connectivity"):
        r = await pub.get(path, headers={"X-API-Key": viewer})
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
    for method in ("POST", "DELETE"):
        r = await pub.request(method, "/v1/ops/errors/clear", headers={"X-API-Key": viewer})
        assert r.status_code == 403 and r.json()["code"] == "SCOPE_REQUIRED", (method, r.status_code)
    operator, _ = await new_key(adm, admin_key, "pro", scopes=("ops",))
    assert (await pub.post("/v1/ops/errors/clear", headers={"X-API-Key": operator})).status_code == 200
    assert (await pub.delete("/v1/ops/errors/clear", headers={"X-API-Key": operator})).status_code == 200


def test_qa_001_web_bff_key_gets_read_only_ops():
    from aptlake_api import provision

    assert provision.WEB_SCOPES == ["read", "ops_read"]


async def _drop_cached(apps, route: str) -> None:
    r = apps[0].state.res.redis
    keys = [k async for k in r.scan_iter(f"al:cache:*:{route}:*")]
    if keys:
        await r.delete(*keys)


# QA-009 (개선 제안 A2): 캐시가 빈 순간 같은 요청이 몰리면 요청마다 따로 계산했다
#         (QA 스택: 분포 50건 동시 → ClickHouse 질의 250회). 발행 직후 캐시 키가 바뀌는 순간 부하가 몰린다.
async def test_qa_009_concurrent_identical_requests_compute_once(pub, adm, admin_key, apps, monkeypatch):
    from aptlake_api.features.regions import service

    calls = 0
    orig = service.distribution

    async def counting(*a, **k):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.2)  # 계산이 끝나기 전에 나머지 요청이 도착하게
        return await orig(*a, **k)

    monkeypatch.setattr(service, "distribution", counting)
    key, _ = await new_key(adm, admin_key, "pro")  # 분당 600회 — 30건 동시가 한도(429)에 가려지지 않게
    await _drop_cached(apps, "region_distribution")
    rs = await asyncio.gather(
        *[
            pub.get("/v1/regions/41135/distribution", params={"ym": "2024-07"}, headers={"X-API-Key": key})
            for _ in range(30)
        ]
    )
    assert [r.status_code for r in rs] == [200] * 30
    assert len({r.content for r in rs}) == 1
    assert calls == 1, f"같은 요청 30건이 {calls}번 계산됨"


# QA-009: 기간만 다른 월별 요청이 매번 ClickHouse 를 불렀다. 지역 하나의 월 계열은 수십 행이고 발행 때만 바뀌는데,
#         부하 측정의 무작위 12개월 구간(시군구 256 × 24)은 결과 캐시에 거의 맞지 않아 질의의 절반이 이 경로였다.
async def test_qa_009_months_windows_share_one_series_query(pub, adm, admin_key, apps, monkeypatch):
    seen = _spy_region_month_queries(apps, monkeypatch)
    key, _ = await new_key(adm, admin_key, "pro")
    await _drop_cached(apps, "region_months")
    windows = (("2024-01", "2024-06"), ("2024-03", "2024-12"), ("2024-05", "2024-05"), ("2023-11", "2024-02"))
    got = []
    for a, b in windows:
        r = await pub.get("/v1/regions/41135/months", params={"from": a, "to": b}, headers={"X-API-Key": key})
        assert r.status_code == 200, r.text[:200]
        got.append([(i["dealYm"], i["trades"], i["medianPricePerM2"]) for i in r.json()["items"]])
    seeded = {f"2024-{m:02d}": (95 + m, 1500 + m) for m in range(1, 13)}  # conftest 의 region_month
    assert got == [[(ym, *seeded[ym]) for ym in sorted(seeded) if a <= ym <= b] for a, b in windows]
    assert len(seen) <= 1, f"기간만 다른 요청 4건에 region_month 질의 {len(seen)}회"


def _spy_region_month_queries(apps, monkeypatch) -> list[str]:
    """API 의 ClickHouse 클라이언트로 나간 region_month 질의를 모은다 (어느 저장소 함수를 거치든)."""
    ch, seen = apps[0].state.res.ch, []
    orig = ch.query

    async def spy(sql, *a, **k):
        if "FROM region_month" in sql:
            seen.append(sql)
        return await orig(sql, *a, **k)

    monkeypatch.setattr(ch, "query", spy)
    return seen


# QA-009 회귀 방지: 계열 캐시는 데이터셋 버전이 바뀌면 다시 읽는다 (발행 뒤 옛 값을 내주지 않게).
#         버전이 바뀐 직후 기간이 다른 요청이 몰려도 표 읽기는 한 번이다
async def test_qa_009_months_series_cache_follows_dataset_version(pub, adm, admin_key, apps, monkeypatch):
    seen = _spy_region_month_queries(apps, monkeypatch)
    key, _ = await new_key(adm, admin_key, "pro")
    r = apps[0].state.res.redis
    windows = [(f"2024-{m:02d}", f"2024-{n:02d}") for m, n in ((1, 3), (2, 4), (3, 5), (4, 6), (1, 12), (6, 9))]
    try:
        for ver in ("gold@test.qa009a", "gold@test.qa009b"):
            await r.set("al:ds:ver", ver)
            await asyncio.sleep(1.1)  # 프로세스 안 데이터셋 버전 캐시(1초)
            before = len(seen)
            rs = await asyncio.gather(
                *[
                    pub.get("/v1/regions/41135/months", params={"from": a, "to": b}, headers={"X-API-Key": key})
                    for a, b in windows * 2
                ]
            )
            assert all(x.status_code == 200 and x.headers["X-Dataset-Version"] == ver for x in rs)
            assert len(seen) == before + 1, f"버전이 바뀐 뒤 계열 읽기 {len(seen) - before}회 (기대 1회)"
    finally:
        await r.set("al:ds:ver", "gold@test.1")
        await asyncio.sleep(1.1)
