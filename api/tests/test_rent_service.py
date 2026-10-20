"""전월세 업무 규칙 (ADR-050) — 가짜 저장소로, DB 없이."""

from __future__ import annotations

import datetime as dt
import statistics

import pytest

from aptlake_api.core.problems import ApiError
from aptlake_api.features.market import service as market
from aptlake_api.features.rent import service as rent

D = dt.date
NAMES = {"41135": {"sgg_nm": "성남시 분당구", "full_nm": "경기도 성남시 분당구"}}


class FakeRent:
    def __init__(self, **kw):
        self.kw = kw

    async def window_medians(self, sgg, a, b, months):
        self.asked = (a, b, months)
        return self.kw.get("trade", []), self.kw.get("rent", [])

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
    store = FakeRent(trade=[{"m": m, "n": 30, "med": 1200.0}], rent=[{"m": m, "n": 12, "med": 840.0}])
    body, rows = await rent.jeonse_ratio(store, NAMES, "41135", D(2024, 6, 1), m, is_provisional=lambda _: False)
    assert rows == 0 and store.asked == (D(2024, 6, 1), m, rent.RATIO_WINDOW_MONTHS)
    june, july = body["items"]
    assert june == {"dealYm": "2024-06", "windowFrom": "2024-04", "tradeMedianPerM2": None, "tradeSample": 0,
                    "jeonseMedianPerM2": None, "jeonseSample": 0, "ratioPct": None}  # fmt: skip
    assert july["ratioPct"] == 70.0 and july["windowFrom"] == "2024-05"
    assert body["definition"] == {"windowMonths": 3, "minSample": 5, "unit": "%"}
    with pytest.raises(ApiError):
        await rent.jeonse_ratio(store, NAMES, "99999", m, m, is_provisional=lambda _: False)


async def test_complex_block_bands_ratio_and_window_from_latest_month():
    rents = (
        [{"area": 84.9, "deposit_manwon": 60000 + i * 1000, "monthly_rent_manwon": 0, "is_jeonse": 1, "is_outlier": 0,
          "deposit_ppm2": (60000 + i * 1000) / 84.9} for i in range(4)]
        + [{"area": 84.9, "deposit_manwon": 5000, "monthly_rent_manwon": 150 + i, "is_jeonse": 0, "is_outlier": 0,
            "deposit_ppm2": 5000 / 84.9} for i in range(3)]
        + [{"area": 59.9, "deposit_manwon": 40000, "monthly_rent_manwon": 0, "is_jeonse": 1, "is_outlier": 0,
            "deposit_ppm2": 40000 / 59.9}]
    )  # fmt: skip
    trades = [{"area": 84.9, "ppm2": 1000.0 + i} for i in range(5)] + [{"area": 120.0, "ppm2": 900.0}]
    store = FakeRent(latest=D(2024, 7, 1), rents=rents, trades=trades)
    block = await rent.complex_block(store, "41135", "c_" + "a" * 20)
    assert block["window"] == {"from": "2023-08", "to": "2024-07"} and store.window == (D(2023, 8, 1), D(2024, 7, 31))
    small, mid = block["byArea"]  # 85~135 구간은 매매만 있어 싣지 않는다
    assert small["band"] == "60m² 미만" and small["ratioPct"] is None  # 매매 표본 0
    assert mid["band"] == "60~85m²" and (mid["jeonse"], mid["wolse"], mid["tradeSample"]) == (4, 3, 5)
    jp = statistics.median([(60000 + i * 1000) / 84.9 for i in range(4)])
    assert mid["ratioPct"] == round(jp / 1002.0 * 100, 1) and mid["wolseMedianRent"] == 151.0
    assert await rent.complex_block(FakeRent(latest=None), "41135", "c_x") is None  # 전월세 발행 전


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
    trade = [{"sgg_cd": "41135", "n": 40, "med": 1200.0}, {"sgg_cd": "11110", "n": 3, "med": 1500.0}]
    rents = [{"sgg_cd": "41135", "n": 9, "med": 600.0}, {"sgg_cd": "11110", "n": 20, "med": 900.0},
             {"sgg_cd": "26110", "n": 20, "med": 500.0}]  # fmt: skip
    assert market.jeonse_ratios(trade, rents) == {"41135": 50.0, "11110": None, "26110": None}
