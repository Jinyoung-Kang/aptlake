"""서비스의 날짜 규칙 (한국 시각 기준)."""

from __future__ import annotations

import datetime as dt

from .settings import settings

KST = dt.timezone(dt.timedelta(hours=9))


def kst_today() -> dt.date:
    """서비스의 '오늘' — 컨테이너 시계(UTC)가 아니라 한국 날짜. 캐시 키의 날짜와 같은 기준이어야 한다."""
    return dt.datetime.now(tz=KST).date()


def provisional(month: dt.date) -> bool:
    """계약월 말일 + provisional_days 전이면 잠정 (신고가 더 들어올 수 있음). 날짜는 KST 기준."""
    nxt = dt.date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return kst_today() < (nxt - dt.timedelta(days=1)) + dt.timedelta(days=settings().provisional_days)
