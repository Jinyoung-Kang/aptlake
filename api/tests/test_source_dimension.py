"""원천(매매·전월세) 차원의 API 계약 — 실제 PostgreSQL·Redis·ClickHouse 로 (ADR-050).

- 전세가율·전월세 월별·시장 지도의 창 완결은 '자료가 발행된 적이 있는가'(적재 기록 + 발행 기록)로 판단한다 —
  수집 상태만 바뀌는 동안(재확인·재수집 실패·발행 대기)에는 같은 버전의 결과 캐시·ETag 와 새로 계산한 값이 어긋나지 않는다
- dataAsOf·X-Data-As-Of 는 응답 원천의 기준 시각 (al:ds:asof:trade·al:ds:asof:rent, 'all' = 이른 쪽)
- 운영 지표 신선도는 원천별 (매매 발행이 멈추면 전월세 발행이 가리지 않는다)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import time
import uuid

import psycopg
from helpers import client_at, fake_dagster, new_key
from psycopg.rows import dict_row

WINDOW = ("202405", "202406", "202407")
RATIO_ROUTES = ("jeonse_ratio", "rent_months", "market_overview")


async def _drop_cached(apps, *routes: str) -> None:
    """결과 캐시를 지운다 — 같은 버전에서 새로 계산한 값이 캐시에 있던 값과 같은지 확인할 때만 쓴다."""
    redis = apps[0].state.res.redis
    for route in routes:
        async for k in redis.scan_iter(f"al:cache:*:{route}:*"):
            await redis.delete(k)


async def _set_version(apps, ver: str) -> None:
    """발행 = 버전 증가. 파이프라인처럼 Redis 현재 버전만 바꾼다 (결과 캐시는 지우지 않는다)."""
    await apps[0].state.res.redis.set("al:ds:ver", ver)
    await asyncio.sleep(1.1)  # 프로세스 안 데이터셋 버전 캐시(1초)


def _partition(pg, source: str, ym: str, status: str, changed: dt.datetime | None, rows_prev: int | None) -> None:
    pg.execute(
        """INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym, status, rows_last, rows_prev, last_changed_at)
           VALUES (%s, '41135', %s, %s, CASE WHEN %s::timestamptz IS NULL THEN NULL ELSE 20 END, %s, %s)
           ON CONFLICT (source, sgg_cd, deal_ym) DO UPDATE SET status = EXCLUDED.status, rows_last = EXCLUDED.rows_last,
               rows_prev = EXCLUDED.rows_prev, last_changed_at = EXCLUDED.last_changed_at""",
        (source, ym, status, changed, rows_prev, changed),
    )


def _publish(pg, source: str, months: list[str], at: dt.datetime) -> None:
    """달 발행 기록 (파이프라인은 이 기록을 커밋한 다음에 Redis 버전을 올린다)."""
    pg.execute(
        """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots, row_counts, source)
           VALUES (%s, %s, %s, %s, '{}', '{}', %s)""",
        (f"gold@sd.{uuid.uuid4().hex[:12]}", at, at, months, source),
    )


async def test_window_completeness_changes_with_publishes_not_ingest_state(apps, stack, adm, admin_key):
    key, _ = await new_key(adm, admin_key, "pro")
    now = dt.datetime.now(tz=dt.UTC)
    published = now - dt.timedelta(days=2)
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        mine = "SELECT * FROM ops.ingest_partition WHERE sgg_cd = '41135' AND deal_ym = ANY(%s)"
        saved = pg.cursor(row_factory=dict_row).execute(mine, (list(WINDOW),)).fetchall()
        for src in ("rtms", "rent"):
            for ym in WINDOW:  # 적재본을 받은 뒤 발행됐다
                _partition(pg, src, ym, "MERGED", published - dt.timedelta(hours=1), None)
        _publish(pg, "trade", list(WINDOW), published)
        _publish(pg, "rent", list(WINDOW), published)
    await _set_version(apps, "gold@sd.1")  # 다른 시험이 같은 버전으로 남긴 결과 캐시와 섞이지 않게
    try:
        async with client_at(apps[0], "10.71.0.1", key) as c:

            async def fetch():
                ratio = await c.get("/v1/regions/41135/jeonse-ratio", params={"from": "2024-07", "to": "2024-07"})
                months = await c.get("/v1/regions/41135/rent/months", params={"from": "2024-05", "to": "2024-07"})
                market = await c.get("/v1/market/overview", params={"ym": "2024-07"})
                assert ratio.status_code == months.status_code == market.status_code == 200
                sgg = next(s for s in market.json()["sgg"] if s["sggCd"] == "41135")
                return {
                    "ratio": ratio.json()["items"][0],
                    "uncollected": months.json()["uncollectedMonths"],
                    "map": sgg["jeonseRatio"],
                    "etags": (ratio.headers["etag"], months.headers["etag"], market.headers["etag"]),
                    "cache": (ratio.headers["x-cache"], months.headers["x-cache"], market.headers["x-cache"]),
                }

            first = await fetch()
            assert first["ratio"]["complete"] is True and first["ratio"]["ratioPct"] == 61.3 and first["map"] == 61.3
            assert first["uncollected"] == []

            # 같은 버전, 수집 기록만 바뀐다 (발행 없음): 매매 7월 재확인 중(FETCHING), 매매 6월 재수집 검사 실패(격리),
            # 전월세 5월 예산 소진(RETRY), 전월세 6월 늦은 신고 적재(mark_loaded — 받은 시각·그 전 행 수), 두 달 발행 대기
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                for src, ym, status in (("rtms", "202407", "FETCHING"), ("rtms", "202406", "QUARANTINED"),
                                        ("rent", "202405", "RETRY")):  # fmt: skip
                    pg.execute(
                        """UPDATE ops.ingest_partition SET status = %s
                           WHERE source = %s AND sgg_cd = '41135' AND deal_ym = %s""",
                        (status, src, ym),
                    )
                _partition(pg, "rent", "202406", "MERGED", now, 20)
                pg.execute(
                    """INSERT INTO ops.month_state (source, deal_ym, needs_publish)
                       VALUES ('rent', '202406', true), ('rtms', '202407', true)
                       ON CONFLICT (source, deal_ym) DO UPDATE SET needs_publish = true"""
                )
            cached = await fetch()
            assert cached["cache"] == ("hit", "hit", "hit")
            assert {k: cached[k] for k in ("ratio", "uncollected", "map", "etags")} == {
                k: first[k] for k in ("ratio", "uncollected", "map", "etags")
            }
            # 캐시에 있던 값이 맞는 값이다 — 비우고 새로 계산해도 같다 (서빙 자료는 마지막 발행 그대로)
            await _drop_cached(apps, *RATIO_ROUTES)
            fresh = await fetch()
            assert fresh["cache"] == ("miss", "miss", "miss")
            assert (fresh["ratio"], fresh["uncollected"], fresh["map"]) == (first["ratio"], [], 61.3)

            # 수집 전이던 전월세 7월 (새 버전에서 시작): 창 미완결
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                _partition(pg, "rent", "202407", "RETRY", None, None)
            await _set_version(apps, "gold@sd.2")
            before = await fetch()
            assert before["ratio"]["complete"] is False and before["ratio"]["ratioPct"] is None
            assert before["ratio"]["tradeMedianPerM2"] is None and before["ratio"]["jeonseMedianPerM2"] is None
            assert (before["ratio"]["tradeSample"], before["ratio"]["jeonseSample"]) == (
                first["ratio"]["tradeSample"],
                first["ratio"]["jeonseSample"],
            )  # 표본 수는 발행된 행 그대로
            assert before["uncollected"] == ["2024-07"] and before["map"] is None
            # 첫 적재본을 받았지만 발행 전 — 같은 버전에서 새로 계산해도 그대로 미완결
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                _partition(pg, "rent", "202407", "LOADED", dt.datetime.now(tz=dt.UTC), None)
            await _drop_cached(apps, *RATIO_ROUTES)
            loaded = await fetch()
            assert (loaded["ratio"], loaded["uncollected"], loaded["map"]) == (
                before["ratio"],
                before["uncollected"],
                before["map"],
            )
            # 발행: 기록을 커밋한 뒤 버전이 오른다 → 새 버전의 응답은 완결
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                _publish(pg, "rent", ["202407"], dt.datetime.now(tz=dt.UTC))
            await _set_version(apps, "gold@sd.3")
            after = await fetch()
            assert after["cache"] == ("miss", "miss", "miss") and after["etags"] != loaded["etags"]
            assert after["ratio"]["complete"] is True and after["ratio"]["ratioPct"] == 61.3 and after["map"] == 61.3
            assert after["uncollected"] == []
    finally:
        with psycopg.connect(stack["su"], autocommit=True) as pg:
            pg.execute("DELETE FROM ops.ingest_partition WHERE sgg_cd = '41135' AND deal_ym = ANY(%s)", (list(WINDOW),))
            for row in saved:  # 앞 시험이 둔 행은 그대로 되돌린다
                cols = list(row)
                pg.execute(
                    f"INSERT INTO ops.ingest_partition ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    list(row.values()),
                )
            pg.execute("DELETE FROM ops.month_state WHERE deal_ym = ANY(%s)", (list(WINDOW),))
            pg.execute("DELETE FROM ops.dataset_version WHERE version LIKE 'gold@sd.%'")
        await _set_version(apps, "gold@test.1")


# 단지 상세 전월세 블록의 창: 전월세 자료의 마지막 계약월(2024-07)까지 12개월
COMPLEX_WINDOW = [f"2023{m:02d}" for m in range(8, 13)] + [f"2024{m:02d}" for m in range(1, 8)]


async def test_complex_rent_block_window_completeness_follows_publishes(apps, stack, adm, admin_key):
    """단지 전세가율도 지역 전세가율과 같은 완결 규칙 — 단지 시군구의 12개월 창, 두 원천. 발행과 함께만 바뀐다."""
    key, _ = await new_key(adm, admin_key, "pro")
    published = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=2)
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        mine = "SELECT * FROM ops.ingest_partition WHERE sgg_cd = '41135' AND deal_ym = ANY(%s)"
        saved = pg.cursor(row_factory=dict_row).execute(mine, (COMPLEX_WINDOW,)).fetchall()
        for src in ("rtms", "rent"):
            for ym in COMPLEX_WINDOW:  # 적재본을 받은 뒤 발행됐다
                _partition(pg, src, ym, "MERGED", published - dt.timedelta(hours=1), None)
        _publish(pg, "trade", COMPLEX_WINDOW, published)
        _publish(pg, "rent", COMPLEX_WINDOW, published)
    await _set_version(apps, "gold@sdc.1")
    try:
        async with client_at(apps[0], "10.71.0.3", key) as c:

            async def block() -> dict:
                r = await c.get("/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa")
                assert r.status_code == 200, r.text
                return r.json()["rent"]

            first = await block()
            assert first["window"] == {"from": "2023-08", "to": "2024-07", "complete": True}
            assert [b["ratioPct"] for b in first["byArea"]] == [61.3]

            # 전월세 2023-10 의 첫 적재본이 아직 발행 전 (새 버전에서 시작) → 창 미완결: 비율만 null
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                _partition(pg, "rent", "202310", "LOADED", dt.datetime.now(tz=dt.UTC), None)
            await _set_version(apps, "gold@sdc.2")
            loaded = await block()
            assert loaded["window"]["complete"] is False and [b["ratioPct"] for b in loaded["byArea"]] == [None]
            assert [{k: v for k, v in b.items() if k != "ratioPct"} for b in loaded["byArea"]] == [
                {k: v for k, v in b.items() if k != "ratioPct"} for b in first["byArea"]
            ]  # 계약 수·중앙값은 발행된 계약 그대로

            # 그 달 발행: 기록을 커밋한 뒤 버전이 오른다 → 새 버전의 응답은 완결
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                _publish(pg, "rent", ["202310"], dt.datetime.now(tz=dt.UTC))
            await _set_version(apps, "gold@sdc.3")
            assert await block() == first
    finally:
        with psycopg.connect(stack["su"], autocommit=True) as pg:
            pg.execute(
                "DELETE FROM ops.ingest_partition WHERE sgg_cd = '41135' AND deal_ym = ANY(%s)", (COMPLEX_WINDOW,)
            )
            for row in saved:  # 앞 시험이 둔 행은 그대로 되돌린다
                cols = list(row)
                pg.execute(
                    f"INSERT INTO ops.ingest_partition ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                    list(row.values()),
                )
            pg.execute("DELETE FROM ops.dataset_version WHERE version LIKE 'gold@sd.%'")
        await _set_version(apps, "gold@test.1")


TRADE_AT = "2026-10-21T08:00:00+00:00"
RENT_AT = "2026-10-21T06:30:00+00:00"
LEGACY_AT = "2030-01-01T00:00:00+00:00"  # 옛 전역 키 값 — 어느 원천의 시각인지 알 수 없어 응답에 나가면 안 된다
# 전환 구간 시험의 발행 기록 — 다른 시험이 남긴 기록보다 늦어 원천별 최댓값이 된다
REC_TRADE = dt.datetime(2031, 1, 2, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
REC_RENT = dt.datetime(2031, 1, 1, 0, 0, tzinfo=dt.UTC)


async def _set_as_of(apps, **values: str | None) -> None:
    redis = apps[0].state.res.redis
    for k, v in values.items():
        if v is None:
            await redis.delete(k)
        else:
            await redis.set(k, v)
    await asyncio.sleep(1.1)  # 프로세스 안 데이터셋 버전 캐시(1초)
    async for k in redis.scan_iter(
        "al:cache:*"
    ):  # 기준 시각은 발행(버전)과 함께 바뀐다 — 버전 그대로 바꿨으니 캐시도 비운다
        await redis.delete(k)


async def test_data_as_of_follows_the_response_source(apps, stack, adm, admin_key):
    key, _ = await new_key(adm, admin_key, "pro", scopes=("read", "ops"))
    # 옛 전역 키는 남아 있어도 쓰지 않는다 (어느 원천의 시각인지 알 수 없다)
    await _set_as_of(apps, **{"al:ds:asof:trade": TRADE_AT, "al:ds:asof:rent": RENT_AT, "al:ds:asof": LEGACY_AT})
    cases = [
        ("/v1/regions/41135/months", {"from": "2024-01", "to": "2024-03"}, TRADE_AT),
        ("/v1/trades", {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 5}, TRADE_AT),
        ("/v1/regions/41135/rent/months", {"from": "2024-01", "to": "2024-03"}, RENT_AT),
        ("/v1/regions/41135/rent/distribution", {"ym": "2024-07"}, RENT_AT),
        ("/v1/rents", {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 5}, RENT_AT),
        ("/v1/rents/r41135-202407-0000000000000000-0/history", {}, RENT_AT),
        ("/v1/regions/41135/jeonse-ratio", {"from": "2024-07", "to": "2024-07"}, RENT_AT),  # 이른 쪽
        ("/v1/market/overview", {"ym": "2024-07"}, RENT_AT),
        ("/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa", {}, RENT_AT),
        ("/v1/quality/summary", {"source": "rtms"}, TRADE_AT),
        ("/v1/quality/summary", {"source": "rent"}, RENT_AT),
        ("/v1/quality/rollup", {"from": "2024-07", "to": "2024-08", "source": "rent"}, RENT_AT),
        ("/v1/ops/status", {}, RENT_AT),
    ]
    public = apps[0]
    real = public.state.dagster
    public.state.dagster = fake_dagster(time.time())
    try:
        async with client_at(apps[0], "10.71.0.2", key) as c:
            for path, params, expected in cases:
                r = await c.get(path, params=params)
                assert r.status_code == 200, (path, r.text)
                assert r.headers["x-data-as-of"] == r.json()["dataAsOf"] == expected, (path, params)
            # 원천별 키를 쓰는 파이프라인이 발행했는데(옛 키는 지운다) 전월세 키가 없으면: 전월세 응답과 두 원천을 함께 쓰는
            # 응답은 null (다른 원천 값으로 채우지 않는다)
            await _set_as_of(apps, **{"al:ds:asof:rent": None, "al:ds:asof": None})
            for path, params, expected in cases:
                r = await c.get(path, params=params)
                want = TRADE_AT if expected == TRADE_AT else None
                assert r.json()["dataAsOf"] == want and r.headers["x-data-as-of"] == (want or ""), (path, params)
            # 전환 구간 — 옛 파이프라인(전역 키만 쓴다)만 발행한 상태: 옛 키 값이 아니라 발행 기록의 원천별 기준 시각
            with psycopg.connect(stack["su"], autocommit=True) as pg:
                for src, at in (("trade", REC_TRADE), ("rent", REC_RENT)):
                    pg.execute(
                        """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots,
                               row_counts, source) VALUES (%s, now(), %s, ARRAY['209901'], '{}', '{}', %s)""",
                        (f"gold@asof.{src}", at, src),
                    )
            await public.state.res.redis.set("al:ds:ver", "gold@asof.1")  # 발행 기록 → 버전 증가 (파이프라인 순서)
            await _set_as_of(apps, **{"al:ds:asof:trade": None, "al:ds:asof": LEGACY_AT})
            rec_trade, rec_rent = (t.astimezone(dt.UTC).isoformat() for t in (REC_TRADE, REC_RENT))
            for path, params, expected in cases:
                r = await c.get(path, params=params)
                want = rec_trade if expected == TRADE_AT else rec_rent  # 'all' = 이른 쪽 = 전월세
                assert r.json()["dataAsOf"] == want and r.headers["x-data-as-of"] == want, (path, params)
    finally:
        await public.state.dagster.aclose()
        public.state.dagster = real
        with psycopg.connect(stack["su"], autocommit=True) as pg:
            pg.execute("DELETE FROM ops.dataset_version WHERE version LIKE 'gold@asof.%'")
        await public.state.res.redis.set("al:ds:ver", "gold@test.1")
        await _set_as_of(apps, **{"al:ds:asof:trade": None, "al:ds:asof:rent": None, "al:ds:asof": None})


async def test_freshness_metric_is_per_source(apps, stack):
    """kind="publish" 는 매매 발행만 — 전월세 발행이 방금 있었어도 매매 발행이 오래됐으면 그대로 보인다."""
    from aptlake_api.features.ops.metrics import OpsCollector

    now = dt.datetime.now(tz=dt.UTC)
    versions = (("gold@metrics.t", now - dt.timedelta(days=30), "trade"), ("gold@metrics.r", now, "rent"))
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        for ver, at, src in versions:
            pg.execute(
                """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots,
                       row_counts, source) VALUES (%s, %s, %s, ARRAY['209901'], '{}', '{}', %s)""",
                (ver, at, at, src),
            )
        try:
            got = {
                s.labels["kind"]: s.value
                for fam in OpsCollector().collect()
                if fam.name == "aptlake_freshness_seconds"
                for s in fam.samples
            }
            want = pg.execute(
                """SELECT extract(epoch FROM now() - max(published_at) FILTER (WHERE source = 'trade'))::float,
                          extract(epoch FROM now() - max(published_at) FILTER (WHERE source = 'rent'))::float,
                          (SELECT extract(epoch FROM now() - max(last_fetched_at))::float
                           FROM ops.ingest_partition WHERE source = 'rent')
                   FROM ops.dataset_version"""
            ).fetchone()
        finally:
            pg.execute("DELETE FROM ops.dataset_version WHERE version LIKE 'gold@metrics.%'")
    assert {"fetch", "publish", "rent_publish"} <= set(got)
    assert want[0] > 86400 and want[1] < 60  # 전제: 매매 발행은 오래됐고 전월세는 방금 발행했다
    # 매매 발행 신선도를 방금 있었던 전월세 발행이 가리지 않는다 (모든 원천의 최근 발행이면 publish ≈ 0)
    assert abs(got["publish"] - want[0]) < 30 and abs(got["rent_publish"] - want[1]) < 30
    if want[2] is None:
        assert "rent_fetch" not in got  # 수집 기록이 없으면 싣지 않는다 (0 은 '방금'으로 읽힌다)
    else:
        assert abs(got["rent_fetch"] - want[2]) < 30


def test_backfill_reserves_follow_the_pipeline_sources(stack):
    """수집 상태의 백필 예상 — 하루 백필 몫의 입력을 파이프라인과 같은 출처·조건으로 읽는다:
    수집 대상 시군구 = leaf·active (ops_db.leaf_regions), 재확인 대기 = 원천별로 3~12개월 전 계약월에 기한이 된
    MERGED·RETRY 파티션 (definitions._recheck_pending). 운영 표를 비운 채 확인하고 트랜잭션은 되돌린다."""
    from aptlake_api.features.ops.repository import RESERVES_SQL

    with psycopg.connect(stack["su"], autocommit=True) as c, c.transaction(force_rollback=True):
        c.execute("DELETE FROM ops.ingest_partition")
        c.execute("DELETE FROM ops.region")
        c.execute(
            """INSERT INTO ops.region (sgg_cd, sido_cd, sido_nm, sgg_nm, full_nm, is_leaf, active, source, fetched_at)
               VALUES ('41135', '41', '경기도', '성남시 분당구', '경기도 성남시 분당구', true, true, 't', now()),
                      ('11110', '11', '서울특별시', '종로구', '서울특별시 종로구', true, true, 't', now()),
                      ('41130', '41', '경기도', '성남시', '경기도 성남시', false, true, 't', now()),
                      ('99990', '99', '시험', '폐지구', '시험 폐지구', true, false, 't', now())"""
        )  # 수집 대상은 앞의 둘 — 하위 일반구가 있는 시(leaf 아님)·폐지(active 아님)는 빼고 센다

        def part(source: str, months_ago: int, status: str, due: str) -> None:
            c.execute(
                """INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym, status, next_due_at)
                   VALUES (%s, '41135', to_char(date_trunc('month', now()) - make_interval(months => %s), 'YYYYMM'),
                           %s, now() + %s::interval)""",
                (source, months_ago, status, due),
            )

        def reserves(source: str) -> tuple:
            return c.execute(RESERVES_SQL, (source,)).fetchone()

        # 재확인 대상이 아니다: 최근 2개월(증분 몫), 13개월 전, 기한 전, 수집 전(PENDING = 백필)
        for months_ago, status, due in ((2, "MERGED", "-1 hour"), (13, "MERGED", "-1 hour"), (5, "MERGED", "1 day"),
                                        (6, "PENDING", "-1 hour")):  # fmt: skip
            part("rent", months_ago, status, due)
        assert reserves("rent") == (2, False) and reserves("rtms") == (2, False)
        part("rtms", 3, "MERGED", "-1 hour")  # 창의 가까운 끝(3개월 전) — 원천별로 따로 본다
        assert reserves("rtms") == (2, True) and reserves("rent") == (2, False)
        part("rent", 12, "RETRY", "-1 hour")  # 창의 먼 끝(12개월 전), 재시도 대기
        assert reserves("rent") == (2, True)
