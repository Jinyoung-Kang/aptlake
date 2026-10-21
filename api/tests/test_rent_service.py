"""전월세 업무 규칙 (ADR-050) — 가짜 저장소로, DB 없이."""

from __future__ import annotations

import datetime as dt
import statistics

import pytest

from aptlake_api.core import cursor as cursor_mod
from aptlake_api.core.problems import ApiError
from aptlake_api.features.market import service as market
from aptlake_api.features.rent import service as rent

D = dt.date
NAMES = {"41135": {"sgg_nm": "성남시 분당구", "full_nm": "경기도 성남시 분당구"}}


PUB = dt.datetime(2024, 8, 5, 3, 0, tzinfo=dt.UTC)  # 그 달의 마지막 달 발행
BEFORE, AFTER = PUB - dt.timedelta(days=1), PUB + dt.timedelta(hours=1)


def states(sgg, months, *, changed=BEFORE, rows_prev=None, published=PUB, sources=("rtms", "rent")):
    """파티션 적재·발행 기록 행 (저장소 partition_states 의 모양). 기본: 적재본을 마지막 발행 전에 받았다 = 서빙에 있다."""
    return [{"source": src, "sgg_cd": sgg, "deal_ym": ym, "last_changed_at": changed, "rows_prev": rows_prev,
             "published_at": published} for src in sources for ym in months]  # fmt: skip


def patch(rows, source, ym, **kw):
    for r in rows:
        if (r["source"], r["deal_ym"]) == (source, ym):
            r.update(kw)
    return rows


class FakeRent:
    def __init__(self, **kw):
        self.kw = kw

    async def window_medians(self, sgg, a, b, months):
        self.asked = (a, b, months)
        return self.kw.get("trade", []), self.kw.get("rent", [])

    async def partition_states(self, sgg, a, b):
        self.states_asked = (sgg, a, b)
        return self.kw.get("states", [])

    async def months(self, sgg, a, b):
        return self.kw.get("months", [])

    async def page(self, f, after, limit):
        self.calls.append(("page", after, limit))
        return self.kw["rows"][:limit]

    async def summary(self, f):
        self.calls.append(("summary",))
        return self.kw["summary"]

    async def latest_month(self):
        return self.kw.get("latest")

    async def complex_rents(self, sgg, key, a, b):
        self.window = (a, b)
        return self.kw.get("rents", [])

    async def complex_trades(self, sgg, key, a, b):
        return self.kw.get("trades", [])

    async def complex_recent(self, sgg, key, limit):
        return []

    async def versions(self, rent_id, yyyymm):
        return self.kw.get("versions", [])


def test_ratio_needs_both_samples():
    assert rent.ratio_pct(700.0, 1000.0, 5, 5, 5) == 70.0
    assert rent.ratio_pct(700.0, 1000.0, 4, 50, 5) is None  # 전세 표본 부족
    assert rent.ratio_pct(700.0, 1000.0, 50, 4, 5) is None  # 매매 표본 부족
    assert rent.ratio_pct(None, 1000.0, 50, 50, 5) is None


async def test_region_ratio_fills_every_month_and_marks_windows():
    m = D(2024, 7, 1)
    store = FakeRent(trade=[{"m": m, "n": 30, "med": 1200.0}], rent=[{"m": m, "n": 12, "med": 840.0}],
                     states=states("41135", ["202404", "202405", "202406", "202407"]))  # fmt: skip
    body, rows = await rent.jeonse_ratio(store, NAMES, "41135", D(2024, 6, 1), m, is_provisional=lambda _: False)
    assert rows == 0 and store.asked == (D(2024, 6, 1), m, rent.RATIO_WINDOW_MONTHS)
    assert store.states_asked == ("41135", D(2024, 4, 1), m)  # 첫 창의 시작 달부터 한 번에
    june, july = body["items"]
    assert june == {"dealYm": "2024-06", "windowFrom": "2024-04", "complete": True, "tradeMedianPerM2": None,
                    "tradeSample": 0, "jeonseMedianPerM2": None, "jeonseSample": 0, "ratioPct": None}  # fmt: skip
    assert july["ratioPct"] == 70.0 and july["windowFrom"] == "2024-05" and july["complete"] is True
    assert rent.RATIO_COMPLETE_NOTE in body["notes"]
    assert body["definition"] == {"windowMonths": 3, "minSample": 5, "unit": "%"}
    with pytest.raises(ApiError):
        await rent.jeonse_ratio(store, NAMES, "99999", m, m, is_provisional=lambda _: False)


COMPLEX_RENTS = (
    [{"area": 84.9, "deposit_manwon": 60000 + i * 1000, "monthly_rent_manwon": 0, "is_jeonse": 1, "is_outlier": 0,
      "deposit_ppm2": (60000 + i * 1000) / 84.9} for i in range(4)]
    + [{"area": 84.9, "deposit_manwon": 5000, "monthly_rent_manwon": 150 + i, "is_jeonse": 0, "is_outlier": 0,
        "deposit_ppm2": 5000 / 84.9} for i in range(3)]
    + [{"area": 59.9, "deposit_manwon": 40000, "monthly_rent_manwon": 0, "is_jeonse": 1, "is_outlier": 0,
        "deposit_ppm2": 40000 / 59.9}]
)  # fmt: skip
COMPLEX_TRADES = [{"area": 84.9, "ppm2": 1000.0 + i} for i in range(5)] + [{"area": 120.0, "ppm2": 900.0}]
# 단지 창 2023-08~2024-07 의 계약월 (전월세 자료의 마지막 계약월 2024-07 까지 12개월)
COMPLEX_WINDOW = [f"2023{m:02d}" for m in range(8, 13)] + [f"2024{m:02d}" for m in range(1, 8)]


async def test_complex_block_bands_ratio_and_window_from_latest_month():
    store = FakeRent(latest=D(2024, 7, 1), rents=COMPLEX_RENTS, trades=COMPLEX_TRADES,
                     states=states("41135", COMPLEX_WINDOW))  # fmt: skip
    block = await rent.complex_block(store, "41135", "c_" + "a" * 20)
    assert block["window"] == {"from": "2023-08", "to": "2024-07", "complete": True}
    assert store.window == (D(2023, 8, 1), D(2024, 7, 31))
    assert store.states_asked == ("41135", D(2023, 8, 1), D(2024, 7, 1))  # 단지의 시군구, 창 12개월
    small, mid = block["byArea"]  # 85~135 구간은 매매만 있어 싣지 않는다
    assert small["band"] == "60m² 미만" and small["ratioPct"] is None  # 매매 표본 0
    assert mid["band"] == "60~85m²" and (mid["jeonse"], mid["wolse"], mid["tradeSample"]) == (4, 3, 5)
    jp = statistics.median([(60000 + i * 1000) / 84.9 for i in range(4)])
    assert mid["ratioPct"] == round(jp / 1002.0 * 100, 1) and mid["wolseMedianRent"] == 151.0
    assert rent.COMPLEX_COMPLETE_NOTE in block["notes"]
    assert await rent.complex_block(FakeRent(latest=None), "41135", "c_x") is None  # 전월세 발행 전


@pytest.mark.parametrize(
    "source,ym,broken",
    [
        ("rent", "202308", None),  # 창 첫 달 행 없음 — 전월세 백필 전
        ("rtms", "202401", {"last_changed_at": None}),  # 한 번도 적재 안 됨 (수집 전·첫 수집 재시도·격리)
        ("rtms", "202407", {"published_at": None}),  # 마지막 달이 한 번도 발행 안 됨
        ("rent", "202312", {"last_changed_at": AFTER}),  # 첫 적재본이 아직 발행 전
    ],
)
async def test_complex_block_ratio_is_null_for_incomplete_window(source, ym, broken):
    """지역 전세가율과 같은 완결 규칙 — 단지 시군구의 12개월 창에서 어느 달·원천이라도 서빙에 들어간 적이 없으면
    구간별 ratioPct 만 null, window.complete=false. 계약 수·중앙값은 발행된 계약 그대로."""
    rows = states("41135", COMPLEX_WINDOW)
    if broken is None:
        rows = [r for r in rows if (r["source"], r["deal_ym"]) != (source, ym)]
    else:
        patch(rows, source, ym, **broken)
    rows += states("11110", COMPLEX_WINDOW)  # 다른 시군구가 완결이어도 단지의 시군구 기준

    async def block(states_rows):
        store = FakeRent(latest=D(2024, 7, 1), rents=COMPLEX_RENTS, trades=COMPLEX_TRADES, states=states_rows)
        return await rent.complex_block(store, "41135", "c_x")

    full, got = await block(states("41135", COMPLEX_WINDOW)), await block(rows)
    assert got["window"] == {"from": "2023-08", "to": "2024-07", "complete": False}
    assert [b["ratioPct"] for b in got["byArea"]] == [None, None]
    assert [b["ratioPct"] for b in full["byArea"]][1] is not None  # 완결이면 값이 있는 구간
    without_ratio = [[{k: v for k, v in b.items() if k != "ratioPct"} for b in x["byArea"]] for x in (got, full)]
    assert without_ratio[0] == without_ratio[1] and got["contracts"] == full["contracts"] == len(COMPLEX_RENTS)


async def test_history_tracks_mutable_fields():
    ts = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    v1 = {
        "version": 1,
        "valid_from": ts,
        "valid_to": ts,
        "is_current": 0,
        "contract_type": "",
        "contract_term": "",
        "use_rr_right": "",
        "pre_deposit_manwon": None,
        "pre_monthly_rent_manwon": None,
        "build_year": 2009,
    }
    v2 = {**v1, "version": 2, "valid_to": None, "is_current": 1, "contract_type": "갱신", "pre_deposit_manwon": 55000}
    body, n = await rent.history(FakeRent(versions=[v1, v2]), "r41135-202407-0000000000000000-0", 202407)
    assert n == 2 and body["versions"][0]["changes"] == []
    assert body["versions"][1]["changes"] == [{"field": "contractType", "from": None, "to": "갱신"},
                                              {"field": "previousDepositManwon", "from": None, "to": 55000}]  # fmt: skip
    with pytest.raises(ApiError):
        await rent.history(FakeRent(versions=[]), "r41135-202407-0000000000000000-0", 202407)


@pytest.mark.parametrize("bad", ["41135-202407-0000000000000000-0", "r41135-202413-0000000000000000-0", "r4113"])
def test_rent_id_shape(bad):
    assert rent.parse_rent_id("r41135-202407-0123456789abcdef-12") == 202407
    with pytest.raises(ApiError):
        rent.parse_rent_id(bad)


def test_market_jeonse_ratio_uses_the_same_rule():
    trade = [{"sgg_cd": "41135", "n": 40, "med": 1200.0}, {"sgg_cd": "11110", "n": 3, "med": 1500.0},
             {"sgg_cd": "11140", "n": 40, "med": 1500.0}]  # fmt: skip
    rents = [{"sgg_cd": "41135", "n": 9, "med": 600.0}, {"sgg_cd": "11110", "n": 20, "med": 900.0},
             {"sgg_cd": "26110", "n": 20, "med": 500.0}, {"sgg_cd": "11140", "n": 20, "med": 900.0}]  # fmt: skip
    window = ["202405", "202406", "202407"]
    rows = [r for sgg in ("41135", "11110", "26110", "11140") for r in states(sgg, window)]
    rows = [r for r in rows if (r["sgg_cd"], r["source"], r["deal_ym"]) != ("11140", "rent", "202405")]
    done = rent.done_partitions(rows)
    assert market.jeonse_ratios(trade, rents, done, D(2024, 7, 1)) == {
        "41135": 50.0,
        "11110": None,  # 매매 표본 부족
        "26110": None,  # 매매 없음
        "11140": None,  # 표본은 충분하지만 창의 전월세 5월이 미완결
    }


class FakeMarket:
    def __init__(self, states_rows):
        self.states_rows = states_rows

    async def month_bounds(self):
        return D(2024, 1, 1), D(2024, 7, 1)

    async def sgg_months(self, m, pm, py):
        return [{"sgg_cd": "41135", "month": m, "trades": 50, "cancelled": 1, "priced": 40, "median_ppm2": 1200.0,
                 "low_sample": 0}]  # fmt: skip

    async def rollups(self, a, b, py):
        return []

    async def ratio_by_sgg(self, a, b):
        self.ratio_window = (a, b)
        return [{"sgg_cd": "41135", "n": 40, "med": 1200.0}], [{"sgg_cd": "41135", "n": 9, "med": 600.0}]

    async def partition_states(self, a, b):
        self.states_window = (a, b)
        return self.states_rows


@pytest.mark.parametrize("complete,ratio", [(True, 50.0), (False, None)])
async def test_market_overview_map_ratio_is_null_for_incomplete_window(complete, ratio):
    window = ["202405", "202406", "202407"] if complete else ["202406", "202407"]
    store = FakeMarket(states("41135", window))
    body, _ = await market.overview(store, D(2024, 7, 1), is_provisional=lambda _: False)
    assert store.states_window == (D(2024, 5, 1), D(2024, 7, 1)) and store.ratio_window == (
        D(2024, 5, 1),
        D(2024, 7, 31),
    )
    assert body["sgg"][0]["jeonseRatio"] == ratio and "jeonseRatioComplete" not in body["sgg"][0]  # 새 필드 없음
    assert body["notes"] == [market.MAP_RATIO_COMPLETE_NOTE]


@pytest.mark.parametrize(
    "source,ym,broken",
    [
        ("rent", "202405", None),  # 행 없음 — 전월세 백필 전 달 (자료 시작 전과 같다)
        ("rtms", "202406", {"last_changed_at": None}),  # 한 번도 적재 안 됨 — 수집 전·첫 수집 재시도·격리
        ("rent", "202407", {"published_at": None}),  # 그 달이 한 번도 발행 안 됨
        ("rent", "202406", {"last_changed_at": AFTER}),  # 첫 적재본이 아직 발행 전 (그 전 적재본 없음)
    ],
)
async def test_region_ratio_needs_every_window_month_of_both_sources(source, ym, broken):
    """창의 어느 달이라도 서빙에 들어간 적이 없으면 비율·중앙값은 null, complete=false — 표본 수는 발행된 행 수 그대로."""
    m = D(2024, 7, 1)
    rows = states("41135", ["202405", "202406", "202407"])
    if broken is None:
        rows = [r for r in rows if (r["source"], r["deal_ym"]) != (source, ym)]
    else:
        patch(rows, source, ym, **broken)
    store = FakeRent(trade=[{"m": m, "n": 30, "med": 1200.0}], rent=[{"m": m, "n": 12, "med": 840.0}], states=rows)
    body, _ = await rent.jeonse_ratio(store, NAMES, "41135", m, m, is_provisional=lambda _: False)
    assert body["items"] == [{"dealYm": "2024-07", "windowFrom": "2024-05", "complete": False,
                              "tradeMedianPerM2": None, "tradeSample": 30, "jeonseMedianPerM2": None,
                              "jeonseSample": 12, "ratioPct": None}]  # fmt: skip


def test_done_partitions_and_window_rule():
    rows = states("41135", ["202405", "202406"]) + states("11110", ["202406"], changed=AFTER)  # 첫 적재본 발행 전
    rows += states("11110", ["202405"], changed=PUB, sources=("rent",))  # 발행과 같은 시각 = 발행 전에 받은 게 아니다
    done = rent.done_partitions(rows)
    assert done == {("41135", s, ym) for s in ("rtms", "rent") for ym in ("202405", "202406")}
    assert rent.window_complete(done, "41135", D(2024, 6, 1), 2)
    assert not rent.window_complete(done, "41135", D(2024, 7, 1), 2)  # 7월 행 없음
    assert not rent.window_complete(done, "11110", D(2024, 6, 1), 1)
    assert rent.window_complete(done, "41135", D(2024, 6, 1), 2, sources=("rent",))


async def test_completeness_changes_only_with_a_publish():
    """완결은 서빙 자료 기준 — 발행 없이 바뀌는 수집 기록으로는 완결 → 미완결이 되지 않는다 (결과 캐시·ETag 가 버전 기준이라).

    재확인 수집(FETCHING → 원본 같음)·재수집 실패(RETRY·QUARANTINED)는 적재본 기록을 바꾸지 않는다. 한 시군구의 늦은 신고가
    적재되면(mark_loaded: 받은 시각 갱신, rows_prev = 그 전 행 수) 그 시군구도 다른 시군구도 그대로 완결이다.
    수집 전이던 파티션의 첫 적재본은 그 달 발행 뒤에야 완결이 된다."""
    m, window, sggs = D(2026, 10, 1), ["202608", "202609", "202610"], ["11110", "27710", "41135"]
    trade = [{"sgg_cd": g, "n": 40, "med": 1200.0} for g in sggs]
    rents = [{"sgg_cd": g, "n": 40, "med": 840.0} for g in sggs]
    rows = [r for g in sggs for r in states(g, window)]
    patch([r for r in rows if r["sgg_cd"] == "11110"], "rent", "202610", last_changed_at=None)  # 수집 전
    before = market.jeonse_ratios(trade, rents, rent.done_partitions(rows), m)
    assert before == {"11110": None, "27710": 70.0, "41135": 70.0}

    # 발행 없이: 41135 매매 10월 늦은 신고 적재, 11110 전월세 10월 첫 적재
    patch([r for r in rows if r["sgg_cd"] == "41135"], "rtms", "202610", last_changed_at=AFTER, rows_prev=38)
    patch([r for r in rows if r["sgg_cd"] == "11110"], "rent", "202610", last_changed_at=AFTER)
    assert market.jeonse_ratios(trade, rents, rent.done_partitions(rows), m) == before

    # 10월 발행 (두 원천) → 첫 적재본도 서빙에 들어갔다
    for src in ("rtms", "rent"):
        patch(rows, src, "202610", published_at=AFTER + dt.timedelta(minutes=5))
    assert market.jeonse_ratios(trade, rents, rent.done_partitions(rows), m) == {g: 70.0 for g in sggs}


def month_row(m: dt.date, contracts: int = 10):
    return {"month": m, "contracts": contracts, "jeonse": 6, "wolse": contracts - 6, "renewals": 3,
            "new_contracts": 5, "rr_used": 1, "jeonse_priced": 6, "outliers": 0, "jeonse_p25_ppm2": 600.0,
            "jeonse_median_ppm2": 700.0, "jeonse_p75_ppm2": 800.0, "wolse_median_rent": None,
            "wolse_median_deposit": None, "low_sample": 0}  # fmt: skip


async def test_rent_months_lists_uncollected_months_apart_from_zero_contract_months():
    """자료가 발행된 적이 없는 달은 uncollectedMonths 로 — items 에 없는 완결 달은 계약 0건이다."""
    rows = states("41135", ["202401", "202402", "202403"], sources=("rent",))
    # 발행된 뒤 새 적재본(늦은 신고)이 발행을 기다린다 — 서빙에는 그 전 적재본이 있다
    rows += states("41135", ["202404"], changed=AFTER, rows_prev=9, sources=("rent",))
    rows += states("41135", ["202405"], changed=None, sources=("rent",))  # 수집 실패(재시도) — 적재본 없음
    rows += states("41135", ["202406"], changed=AFTER, sources=("rent",))  # 첫 적재본이 아직 발행 전
    rows += states("41135", ["202406"], sources=("rtms",))  # 매매가 발행됐어도 전월세 기준으로는 미수집
    store = FakeRent(months=[month_row(D(2024, 1, 1)), month_row(D(2024, 4, 1))], states=rows)  # 2·3월 계약 0건
    body, n = await rent.rent_months(store, NAMES, "41135", D(2024, 1, 1), D(2024, 6, 1),
                                     is_provisional=lambda _: False, provisional_days=60)  # fmt: skip
    assert n == 0 and [i["dealYm"] for i in body["items"]] == ["2024-01", "2024-04"]
    assert body["uncollectedMonths"] == ["2024-05", "2024-06"]
    assert store.states_asked == ("41135", D(2024, 1, 1), D(2024, 6, 1))
    assert body["items"][0]["typeUnknown"] == 2  # 10 - 갱신 3 - 신규 5
    notes = " ".join(body["notes"])
    assert "의무화" not in notes and "미상(typeUnknown)" in notes and "uncollectedMonths" in notes


async def test_rent_page_cursor_continues_and_is_bound_to_the_query():
    def row(i: int, day: int) -> dict:
        return {"rent_id": f"r41135-202407-{i:016x}-0", "deal_date": D(2024, 7, day), "complex_key": "c_x",
                "apt_nm": "단지", "umd_nm": "동", "jibun": "", "build_year": None, "is_jeonse": 1,
                "deposit_manwon": 60000, "monthly_rent_manwon": 0, "area_m2": 84.9, "floor": 3,
                "deposit_ppm2": 706.7, "contract_type": "", "contract_term": "", "use_rr_right": "",
                "pre_deposit_manwon": None, "pre_monthly_rent_manwon": None, "is_outlier": 0, "version": 1,
                "missing_since": None}  # fmt: skip

    f = rent.RentFilter("41135", D(2024, 7, 1), D(2024, 7, 31), None, None, "jeonse")
    key, fp = b"k", cursor_mod.query_fingerprint(f.key(2))
    store = FakeRent(rows=[row(i, 30 - i) for i in range(3)],
                     summary={"n": 3, "jeonse": 3, "jeonse_dep": 60000.0, "jeonse_ppm2": 706.7,
                              "wolse_rent": float("nan")})  # fmt: skip
    store.calls = []
    body, n = await rent.page(store, f, 2, None, cursor_key=key, fingerprint=fp)
    assert n == 2 and store.calls == [("page", None, 3), ("summary",)]  # 한 건 더 읽어 다음 페이지 여부 판단
    assert body["summary"]["count"] == 3 and body["summary"]["wolseMedianRent"] is None
    pos = rent.read_cursor(key, body["page"]["nextCursor"], fp)
    assert pos == {"d": "2024-07-29", "k": row(1, 29)["rent_id"]}  # 마지막으로 보낸 계약 다음부터
    store.calls.clear()
    body2, _ = await rent.page(store, f, 2, pos, cursor_key=key, fingerprint=fp)
    assert body2["summary"] is None and store.calls == [("page", (D(2024, 7, 29), pos["k"]), 3)]
    for other in (rent.RentFilter("41135", D(2024, 7, 1), D(2024, 7, 31), None, None, "wolse").key(2), f.key(3)):
        with pytest.raises(ApiError) as e:  # 조건(종류·쪽 크기)이 다른 질의의 커서
            rent.read_cursor(key, body["page"]["nextCursor"], cursor_mod.query_fingerprint(other))
        assert e.value.status == 400 and e.value.code == "INVALID_CURSOR"
