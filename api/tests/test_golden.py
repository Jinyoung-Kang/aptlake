"""리팩터링 안전망: 공개 API 의 응답(상태 코드·본문·주요 헤더)을 골든 파일로 고정한다.

계층 분리처럼 동작을 바꾸지 않아야 하는 변경 전후에 응답이 한 글자도 달라지지 않는지 본다.
시각에 따라 바뀌는 값(지금 기준 10일 안의 시각, 오늘 날짜, 틱 ID)만 자리표시로 바꿔 비교한다.

의도한 변경으로 응답이 바뀌면:  UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py  → 차이를 검토하고 커밋
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from helpers import client_at, fake_dagster, new_key

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"
KST = dt.timezone(dt.timedelta(hours=9))
_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
_TICK = re.compile(r"^tick:(.+):[\d.]+$")
HEADERS = ("content-type", "cache-control", "x-dataset-version")

# (이름, 경로, 매개변수)
CASES: list[tuple[str, str, dict]] = [
    ("regions", "/v1/regions", {}),
    ("region_months", "/v1/regions/41135/months", {"from": "2024-01", "to": "2024-12"}),
    ("region_months_unknown", "/v1/regions/99999/months", {"from": "2024-01", "to": "2024-03"}),
    ("region_months_bad_range", "/v1/regions/41135/months", {"from": "2024-05", "to": "2024-01"}),
    (
        "trades_first_page",
        "/v1/trades",
        {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 40, "includeCancelled": "true"},
    ),
    (
        "trades_area_filter",
        "/v1/trades",
        {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-10", "minArea": 80, "maxArea": 90, "limit": 5},
    ),
    ("trades_bad_cursor", "/v1/trades", {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "cursor": "x.y"}),
    ("trade_history", "/v1/trades/41135-202407-0000000000000000-0/history", {}),
    ("trade_history_missing", "/v1/trades/41135-202407-ffffffffffffffff-0/history", {}),
    ("trade_history_bad_id", "/v1/trades/not-a-trade/history", {}),
    ("complex", "/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa", {}),
    ("complex_missing", "/v1/complexes/c_bbbbbbbbbbbbbbbbbbbb", {}),
    ("index_nation", "/v1/index", {"regionId": "00"}),
    ("index_missing", "/v1/index", {"regionId": "11"}),
    ("index_summary", "/v1/index/summary", {}),
    ("quality_summary", "/v1/quality/summary", {}),
    ("quality_grid_sido", "/v1/quality/partitions", {"from": "2024-07", "to": "2024-08", "sido": "41"}),
    ("quality_rollup", "/v1/quality/rollup", {"from": "2024-07", "to": "2024-08"}),
    ("quality_partition", "/v1/quality/partitions/41135/2024-08", {}),
    ("quality_partition_merged", "/v1/quality/partitions/41135/2024-07", {}),
    ("quality_partition_missing", "/v1/quality/partitions/41135/2020-01", {}),
    ("market_overview_default", "/v1/market/overview", {}),
    ("market_overview_month", "/v1/market/overview", {"ym": "2024-06"}),
    ("market_overview_out_of_range", "/v1/market/overview", {"ym": "2030-01"}),
    ("market_ticker", "/v1/market/ticker", {}),
    ("search_complex", "/v1/search", {"q": "테스트"}),
    ("search_dong", "/v1/search", {"q": "백현"}),
    ("distribution", "/v1/regions/41135/distribution", {"ym": "2024-07"}),
    ("distribution_empty", "/v1/regions/41135/distribution", {"ym": "2024-01"}),
    ("region_complexes", "/v1/regions/41135/complexes", {"from": "2024-07", "to": "2024-07", "limit": 10}),
    ("geo_sgg", "/v1/geo/sgg", {}),
    ("ops_status", "/v1/ops/status", {}),
    # API 5xx 출처는 사용량 이벤트가 비동기로 쌓여 시점에 따라 달라지므로 출처별로 나눠 고정한다
    ("ops_errors_pipeline", "/v1/ops/errors", {"hours": 24, "source": "pipeline"}),
    ("ops_errors_pipeline_all", "/v1/ops/errors", {"hours": 168, "source": "pipeline", "includeResolved": "true"}),
    ("ops_errors_ingest", "/v1/ops/errors", {"hours": 168, "source": "ingest"}),
    ("ops_errors_quality", "/v1/ops/errors", {"hours": 168, "source": "quality", "includeResolved": "true"}),
]


def _normalize(v, now: dt.datetime, today: set[str]):
    if isinstance(v, dict):
        return {k: ("<trace>" if k == "traceId" else _normalize(x, now, today)) for k, x in v.items()}
    if isinstance(v, list):
        return [_normalize(x, now, today) for x in v]
    if isinstance(v, str):
        if _TS.match(v):
            t = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.UTC)
            if abs((t - now).total_seconds()) < 10 * 86400:
                return "<recent>"
        if v in today:
            return "<today>"
        if m := _TICK.match(v):
            return f"tick:{m.group(1)}:<ts>"
    return v


@pytest_asyncio.fixture(scope="module")
async def seeded(apps, stack, adm, admin_key):
    """골든 비교용 운영 DB·서빙 DB 상태. 앞서 돈 테스트가 남긴 운영 표 상태와 무관하도록 먼저 비운다."""
    geom = {"type": "MultiPolygon", "coordinates": [[[[127.1, 37.3], [127.2, 37.3], [127.2, 37.4], [127.1, 37.3]]]]}
    fixed = dt.datetime(2024, 8, 5, 3, 0, tzinfo=dt.UTC)
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        pg.execute(
            "TRUNCATE ops.ingest_partition, ops.dq_result, ops.dataset_version, ops.api_budget, ops.region_boundary"
            " RESTART IDENTITY"  # 검사 ID(dq:N)가 앞 테스트가 넣은 행 수에 따라 바뀌지 않게
        )
        pg.execute("UPDATE ops.log_view SET cleared_at = NULL, cleared_by = NULL")
        pg.execute(
            """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, attempts, rows_last, rows_prev,
                   payload_sha256, last_ingest_id, last_fetched_at, last_changed_at, fetch_count, next_due_at, last_error,
                   updated_at)
               VALUES ('41135','202407','MERGED',0,250,248, repeat('b',64),'202407-41135-1',%s,%s,3,%s,NULL,%s),
                      ('41135','202408','QUARANTINED',3,NULL,NULL,NULL,NULL,%s,NULL,0,%s,
                       'HTTP 500 for https://apis.data.go.kr/x?serviceKey=SECRET123&LAWD_CD=41135',%s - interval '1 hour'),
                      ('11110','202407','PENDING',0,NULL,NULL,NULL,NULL,NULL,NULL,0,%s,NULL,%s)""",
            (fixed, fixed, fixed, fixed, fixed, fixed, dt.datetime.now(tz=dt.UTC), fixed, fixed),
        )
        recent = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=2)
        pg.execute(
            """INSERT INTO ops.dq_result (asset, partition, check_name, passed, severity, blocking, metric, at)
               VALUES ('bronze/rtms_raw','41135/202408','schema_v1',false,'ERROR',true,'{"bad_rows":3}',%s),
                      ('silver/apt_trade','202407','dup_free',true,'ERROR',true,'{"dups":0}',%s),
                      ('silver/apt_trade','202407','cancel_ratio_shift',false,'WARN',false,'{"delta_pp":11.2}',%s)""",
            (recent, fixed, recent),
        )
        pg.execute(
            """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots, row_counts)
               VALUES ('gold@test.1', %s, %s, ARRAY['202407'], '{"silver.apt_trade": 42}', '{"trade_current": 250}')""",
            (fixed, fixed),
        )
        pg.execute(
            """INSERT INTO ops.region_boundary (sgg_cd, geometry, area_rel_error, source, source_sha256, simplify, fetched_at)
               VALUES ('41135', %s, 0.002, 'V-World LT_C_ADSIGG_INFO', repeat('c', 64), '{}', %s)""",
            (json.dumps(geom), fixed),
        )
    ch = stack["ch"]
    ch.command("TRUNCATE TABLE IF EXISTS index_reference")
    ch.command("TRUNCATE TABLE IF EXISTS index_validation")
    ch.insert(
        "index_reference",
        [["00", dt.date(2023 + i // 12, i % 12 + 1, 1), 95.0 + i * 0.5, "R-ONE test"] for i in range(24)],
        column_names=["region_id", "period", "value", "source"],
    )
    ch.insert(
        "index_validation",
        [
            [
                "00",
                "HEDONIC_TD_v1",
                "R-ONE test",
                0.91,
                0.83,
                22,
                dt.date(2023, 2, 1),
                dt.date(2024, 11, 1),
            ]
        ],
        column_names=[
            "region_id",
            "method",
            "reference",
            "corr_mom",
            "direction_match",
            "n_months",
            "window_from",
            "window_to",
        ],
    )
    key, _ = await new_key(adm, admin_key, "pro", scopes=("read", "bulk", "ops"))
    public = apps[0]
    redis = public.state.res.redis
    async for k in redis.scan_iter("al:cache:*"):  # 앞 테스트가 남긴 결과 캐시 (같은 데이터셋 버전)
        await redis.delete(k)
    real = public.state.dagster
    public.state.dagster = fake_dagster(time.time())
    yield key
    await public.state.dagster.aclose()
    public.state.dagster = real


async def _snapshot(c, path: str, params: dict) -> dict:
    r = await c.get(path, params=params)
    body = r.json() if r.headers.get("content-type", "").endswith("json") else r.text
    return {
        "status": r.status_code,
        "headers": {h: r.headers.get(h) for h in HEADERS if h in r.headers},
        "etag": "etag" in r.headers,  # 값은 날짜(KST)마다 바뀌므로 있는지만 — 캐시·304 계약
        "body": body,
    }


@pytest.mark.parametrize("name,path,params", CASES, ids=[c[0] for c in CASES])
async def test_public_responses_match_golden(apps, seeded, name, path, params):
    async with client_at(apps[0], "10.99.0.1", seeded) as c:
        snap = await _snapshot(c, path, params)
    now = dt.datetime.now(tz=dt.UTC)
    today = {dt.datetime.now(tz=KST).date().isoformat(), now.date().isoformat()}
    got = _normalize(snap, now, today)
    if name == "trades_first_page":
        # 다음 페이지 커서도 함께 고정 (서명 키가 같으면 커서 문자열도 같다)
        async with client_at(apps[0], "10.99.0.1", seeded) as c:
            nxt = await _snapshot(c, path, {**params, "cursor": snap["body"]["page"]["nextCursor"]})
        got["nextPage"] = _normalize(nxt, now, today)
    file = GOLDEN / f"{name}.json"
    text = json.dumps(got, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if UPDATE or not file.exists():
        GOLDEN.mkdir(exist_ok=True)
        file.write_text(text)
        if not UPDATE:
            pytest.fail(f"골든 파일을 새로 만들었습니다: {file.name} — 내용을 검토하고 다시 실행하세요")
    # 글자 그대로 비교 — 값으로 비교하면 1234 와 1234.0(정수·실수 바뀜)이 같다고 통과했다
    assert file.read_text() == text, f"{name}: 응답이 골든 파일과 다릅니다"
