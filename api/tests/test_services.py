"""업무 규칙(service) 단위 테스트 — DB 없이 가짜 저장소로. 계층 분리의 이점: 규칙만 따로 빠르게 검증한다."""

from __future__ import annotations

import datetime as dt

import pytest

from aptlake_api.core.problems import ApiError
from aptlake_api.features.regions import service as regions


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
