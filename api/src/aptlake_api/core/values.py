"""여러 기능이 쓰는 값 변환 (순수 함수 — 웹 프레임워크·DB 에 의존하지 않음)."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any


def ym_to_date(ym: str) -> dt.date:
    """'YYYY-MM' → 그 달 1일."""
    return dt.date(int(ym[:4]), int(ym[5:7]), 1)


def add_months(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    return dt.date(d.year + m // 12, m % 12 + 1, 1)


def months_between(a: dt.date, b: dt.date) -> int:
    """a·b 를 포함한 달 수."""
    return (b.year - a.year) * 12 + (b.month - a.month) + 1


def month_end(d: dt.date) -> dt.date:
    return add_months(d, 1) - dt.timedelta(days=1)


def rnd(v: float | None, n: int = 1) -> float | None:
    """반올림. 값이 없거나 NaN 이면 None."""
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)


def pct_change(a: float | None, b: float | None) -> float | None:
    """b 대비 a 의 변화율(%), 소수 둘째 자리."""
    if a is None or b is None or b == 0:
        return None
    return round((a / b - 1) * 100, 2)


def ts(v: dt.datetime | None) -> str | None:
    """ClickHouse DateTime64(…, 'UTC') 값은 naive 로 돌아오므로 UTC 를 명시한다."""
    if v is None:
        return None
    return (v if v.tzinfo else v.replace(tzinfo=dt.UTC)).isoformat()


def iso(v: Any) -> str | None:
    return v.isoformat() if v else None
