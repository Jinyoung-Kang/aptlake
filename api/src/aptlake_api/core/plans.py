"""플랜 한도 규칙 (순수)."""

from __future__ import annotations

import datetime as dt

from .problems import ApiError
from .values import months_between


def check_range(plan_id: str, max_months: int | None, start: dt.date, end: dt.date) -> None:
    """조회 기간: 끝이 시작보다 앞이면 400, 플랜의 최대 개월 수를 넘으면 422."""
    if end < start:
        raise ApiError(400, "INVALID_RANGE", "Invalid Range", "to 는 from 이후여야 합니다.")
    if max_months is not None and months_between(start, end) > max_months:
        raise ApiError(422, "RANGE_EXCEEDS_PLAN", "Range Too Large", f"{plan_id} 플랜은 최대 {max_months}개월")
