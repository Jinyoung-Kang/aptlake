"""업무 규칙(service) 단위 테스트 — DB 없이 가짜 저장소로. 계층 분리의 이점: 규칙만 따로 빠르게 검증한다."""

from __future__ import annotations

import datetime as dt

import pytest

from aptlake_api.core import cursor as cursor_mod
from aptlake_api.core.problems import ApiError
from aptlake_api.features.regions import service as regions
from aptlake_api.features.trades import service as trades


class FakeRegions:
    def __init__(self, months=None):
        self._months = months or []

    async def months(self, sgg, a, b):
        return self._months


def month_row(m: dt.date, **kw):
    base = dict(reported=10, trades=9, cancelled=1, priced=9, outliers=0, p25_ppm2=1000.04, median_ppm2=1100.06)
    return {"month": m, **base, "p75_ppm2": None, "low_sample": 0, **kw}


async def test_region_months_flags_and_unknown_region():
    names = {"41135": {"sgg_nm": "성남시 분당구", "full_nm": "경기도 성남시 분당구"}}
    store = FakeRegions([month_row(dt.date(2024, 1, 1)), month_row(dt.date(2024, 2, 1), low_sample=1)])
    body, rows = await regions.region_months(
        store,
        names,
        "41135",
        dt.date(2024, 1, 1),
        dt.date(2024, 2, 1),
        is_provisional=lambda m: m.month == 2,
        provisional_days=60,
    )
    first, second = body["items"]
    assert (first["dealYm"], first["p25PricePerM2"], first["medianPricePerM2"], first["p75PricePerM2"]) == (
        "2024-01",
        1000.0,
        1100.1,
        None,
    )
    assert "lowSample" not in first and "provisional" not in first
    assert second["lowSample"] is True and second["provisional"] is True
    assert rows == 0 and "60일" in body["notes"][2]
    with pytest.raises(ApiError) as e:
        await regions.region_months(
            store, names, "11110", dt.date(2024, 1, 1), dt.date(2024, 2, 1), is_provisional=bool, provisional_days=60
        )
    assert e.value.code == "INVALID_REGION"


@pytest.mark.parametrize(
    ("p05", "p95", "width"),
    [(1000.0, 2600.0, 100.0), (900.0, 1300.0, 25.0), (None, None, 0.1), (1000.0, 1000.5, 0.1), (0.0, 32000.0, 2000.0)],
)
def test_histogram_width_is_a_nice_number_near_one_sixteenth_of_the_range(p05, p95, width):
    assert regions.histogram_width(p05, p95) == pytest.approx(width)


def test_period_must_not_be_reversed():
    regions.check_period(dt.date(2024, 1, 1), dt.date(2024, 1, 31))
    with pytest.raises(ApiError):
        regions.check_period(dt.date(2024, 2, 1), dt.date(2024, 1, 31))


# ───── 거래 ─────


def trade_row(i: int, day: int) -> dict:
    return {
        "trade_id": f"41135-202407-{i:016x}-0",
        "deal_date": dt.date(2024, 7, day),
        "complex_key": "c_" + "a" * 20,
        "apt_nm": "A",
        "umd_nm": "B",
        "jibun": "",
        "area_m2": 84.9,
        "floor": 5,
        "price_manwon": 100000,
        "ppm2": 1177.0,
        "is_cancelled": 0,
        "cancel_date": None,
        "registered_date": None,
        "apt_dong": "",
        "deal_kind": "",
        "seller_type": "",
        "buyer_type": "",
        "build_year": None,
        "is_outlier": 0,
        "version": 1,
        "missing_since": None,
    }


class FakeTrades:
    def __init__(self, rows, summary):
        self.rows, self._summary, self.calls = rows, summary, []

    async def page(self, f, after, limit):
        self.calls.append(("page", after, limit))
        return self.rows[:limit]

    async def summary(self, f):
        self.calls.append(("summary",))
        return self._summary


async def test_trade_page_cursor_and_summary_only_on_first_page():
    f = trades.TradeFilter("41135", dt.date(2024, 7, 1), dt.date(2024, 7, 31), None, None, True)
    key, fp = b"k", cursor_mod.query_fingerprint(f.key(2))
    store = FakeTrades(
        [trade_row(i, 30 - i) for i in range(3)], {"n": 3, "cancelled": 0, "med": 1177.04, "med_price": 1e5}
    )
    body, rows = await trades.page(store, f, 2, None, cursor_key=key, fingerprint=fp)
    assert rows == 2 and store.calls == [("page", None, 3), ("summary",)]  # 한 건 더 읽어 다음 페이지 여부 판단
    assert body["summary"] == {"count": 3, "cancelled": 0, "medianPpm2": 1177.0, "medianPrice": 100000}
    pos = trades.read_cursor(key, body["page"]["nextCursor"], fp)
    assert pos == {"d": "2024-07-29", "k": trade_row(1, 29)["trade_id"]}
    store.calls.clear()
    body2, _ = await trades.page(store, f, 2, pos, cursor_key=key, fingerprint=fp)
    assert body2["summary"] is None and store.calls == [("page", (dt.date(2024, 7, 29), pos["k"]), 3)]
    with pytest.raises(ApiError):  # 다른 질의의 커서
        trades.read_cursor(key, body["page"]["nextCursor"], cursor_mod.query_fingerprint(f.key(3)))


def test_trade_summary_without_valid_trades_has_no_median():
    assert trades._summary({"n": 1, "cancelled": 1, "med": float("nan"), "med_price": float("nan")}) == {
        "count": 1,
        "cancelled": 1,
        "medianPpm2": None,
        "medianPrice": None,
    }


def test_trade_id_and_page_size_rules():
    assert trades.parse_trade_id("41135-202407-00000000000000ff-0") == 202407
    with pytest.raises(ApiError):
        trades.parse_trade_id("41135-2024-07-x")
    trades.check_page_size("free", 200, 200)
    with pytest.raises(ApiError) as e:
        trades.check_page_size("free", 200, 201)
    assert e.value.status == 422


# ───── 내보내기 ─────
class FakeExports:
    def __init__(self, room=True, job=None):
        self.room, self.job, self.created = room, job, []

    async def create_if_room(self, client_id, key_id, params_json, max_running):
        self.created.append(max_running)
        return "11111111-1111-1111-1111-111111111111" if self.room else None

    async def get(self, job_id, client_id):
        return self.job


async def test_export_rules():
    from aptlake_api.features.exports import service as exports

    with pytest.raises(ApiError) as e:
        exports.check_request(False, dt.date(2024, 1, 1), dt.date(2024, 1, 2))
    assert e.value.code == "PLAN_NOT_ALLOWED"
    with pytest.raises(ApiError) as e:
        exports.check_request(True, dt.date(2024, 1, 2), dt.date(2024, 1, 1))
    assert e.value.code == "INVALID_RANGE"
    store = FakeExports(room=False)
    with pytest.raises(ApiError) as e:
        await exports.create(store, "c", "k", "{}")
    assert e.value.status == 429 and store.created == [exports.MAX_RUNNING]

    done = {
        "job_id": "j",
        "status": "done",
        "rows": 5,
        "error": None,
        "object_key": "c/j.parquet",
        "created_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "finished_at": None,
    }
    out = await exports.status(FakeExports(job=done), "j", "c", presign=lambda k: f"https://s3/{k}", url_ttl_s=900)
    assert out["downloadUrl"] == "https://s3/c/j.parquet" and out["expiresInSeconds"] == 900
    with pytest.raises(ApiError) as e:
        await exports.status(FakeExports(job=None), "j", "c", presign=str, url_ttl_s=900)
    assert e.value.status == 404


# ───── 운영: 오류 로그 ─────
class SlowOps:
    """출처마다 0.3초 걸리는 가짜 저장소·오케스트레이터 (장애로 느려진 상황)."""

    delay = 0.3

    async def _slow(self, value):
        import asyncio

        await asyncio.sleep(self.delay)
        return value

    async def log_view(self):
        return None

    async def runs(self, statuses=None, created_after=None, limit=50):
        return await self._slow([])

    async def instigators(self):
        return {"schedules": [], "sensors": []}

    async def ingest_errors(self, since):
        return await self._slow([])

    async def budget_exhaustions(self, since):
        return []

    async def quality_failures(self, since):
        return await self._slow([])

    async def api_5xx(self, since):
        return await self._slow([])

    async def api_error_summary(self, since):
        return []


async def test_error_log_sources_are_read_concurrently():
    """한 출처가 느려도(장애) 전체 시간은 출처 시간의 합이 아니라 가장 느린 것 정도 — 예전엔 34~70초 기록."""
    import time

    from aptlake_api.features.ops import service as ops

    fake = SlowOps()
    t = time.perf_counter()
    body, _ = await ops.errors(
        fake,
        fake,
        hours=24,
        source="all",
        include_resolved=False,
        include_cleared=False,
        now=dt.datetime(2026, 10, 3, tzinfo=dt.UTC),
    )
    elapsed = time.perf_counter() - t
    assert body["entries"] == [] and body["notes"] == []
    assert elapsed < 0.3 * 4 * 0.6, f"{elapsed:.2f}s — 출처를 차례로 읽는다"


async def test_error_log_keeps_section_order_and_notes():
    """병렬로 읽어도 같은 시각 항목의 순서·안내 문구 순서는 출처 순서(파이프라인 → 수집 → 품질 → API) 그대로."""
    from aptlake_api.features.ops import service as ops

    at = dt.datetime(2026, 10, 2, 12, tzinfo=dt.UTC)

    class Down(SlowOps):
        delay = 0.0

        async def runs(self, statuses=None, created_after=None, limit=50):
            raise ops.DagsterUnavailable("ConnectError")

        async def ingest_errors(self, since):
            return [
                {"deal_ym": "202407", "status": "RETRY", "last_error": "boom", "n": 1, "at": at, "sample": ["11110"]}
            ]

        async def quality_failures(self, since):
            return [
                {"check_id": 1, "asset": "a", "partition": None, "check_name": "c", "blocking": False,
                 "metric": {}, "at": at, "resolved": False}
            ]  # fmt: skip

        async def api_5xx(self, since):
            raise ops.SourceUnavailable("TIMEOUT_EXCEEDED")

    fake = Down()
    body, _ = await ops.errors(
        fake, fake, hours=24, source="all", include_resolved=False, include_cleared=False, now=at
    )
    assert [e["source"] for e in body["entries"]] == ["ingest", "quality"]
    assert ["Dagster" in body["notes"][0], "TIMEOUT_EXCEEDED" in body["notes"][1]] == [True, True]


# ───── 시장 개요 ─────
class FakeMarket:
    def __init__(self, sgg_rows):
        self.sgg_rows = sgg_rows

    async def month_bounds(self):
        return dt.date(2023, 1, 1), dt.date(2024, 7, 1)

    async def sgg_months(self, m, pm, py):
        return self.sgg_rows

    async def rollups(self, a, b, py):
        return []


def sgg_row(code: str, month: dt.date, trades: int, median: float) -> dict:
    return {"sgg_cd": code, "month": month, "trades": trades, "cancelled": 0, "priced": 50,
            "median_ppm2": median, "low_sample": 0}  # fmt: skip


async def test_market_rankings_do_not_depend_on_row_order():
    """동점(거래 수·변화율 같음)이면 시군구 코드 순 — 질의 결과 순서(엔진 병합 순서)에 따라 순위가 바뀌면 안 된다."""
    from aptlake_api.features.market import service as market

    m, py = dt.date(2024, 7, 1), dt.date(2023, 7, 1)
    codes = [f"{41000 + i * 5}" for i in range(12)]
    rows = [r for c in codes for r in (sgg_row(c, m, 100, 1100.0), sgg_row(c, py, 100, 1000.0))]
    a, _ = await market.overview(FakeMarket(rows), m, is_provisional=lambda _: False)
    b, _ = await market.overview(FakeMarket(rows[::-1]), m, is_provisional=lambda _: False)
    for k in ("volume", "gainers", "losers"):
        assert [s["sggCd"] for s in a["rankings"][k]] == codes[:10], k
        assert a["rankings"][k] == b["rankings"][k], k
    assert [s["sggCd"] for s in a["sgg"]] == [s["sggCd"] for s in b["sgg"]] == codes


async def test_error_log_keeps_run_failures_when_only_sensor_query_fails():
    """실행 목록은 읽었는데 스케줄·센서 질의만 실패해도 실행 실패 항목은 보여 준다 (예전 동작) — 안내 문구와 함께."""
    from aptlake_api.features.ops import service as ops

    at = dt.datetime(2026, 10, 2, 12, tzinfo=dt.UTC)
    run = {"runId": "r-1", "jobName": "month_pipeline", "status": "FAILURE", "creationTime": at.timestamp(),
           "startTime": at.timestamp(), "endTime": at.timestamp() + 60, "tags": [{"key": "dagster/partition", "value": "202407"}]}  # fmt: skip

    class SensorDown(SlowOps):
        delay = 0.0

        async def runs(self, statuses=None, created_after=None, limit=50):
            return [run] if statuses == ["FAILURE"] else []

        async def run_errors(self, run_id):
            return [{"at": None, "step": "bronze__rtms", "error": {"message": "boom"}}]

        async def instigators(self):
            raise ops.DagsterUnavailable("ReadTimeout")

    fake = SensorDown()
    body, _ = await ops.errors(
        fake, fake, hours=24, source="pipeline", include_resolved=False, include_cleared=False, now=at
    )
    assert [e["ref"] for e in body["entries"]] == ["r-1"]
    assert len(body["notes"]) == 1 and "Dagster" in body["notes"][0]
