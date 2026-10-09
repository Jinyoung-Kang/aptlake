"""플랜과 내 사용량 — 업무 규칙 (순수). 조회 대상은 항상 인증된 키의 클라이언트 (요청으로 다른 클라이언트를 지정할 수 없음).

기준이 둘이다 (ADR-047): 하루 행 한도는 **키** 단위로 세고, 일별·달별 사용량은 **고객**(그 고객의 모든 키) 단위로 묶는다.
금액은 보이지 않는다 — 근거 있는 단가가 없다(ADR-018). 과금 대상이 될 단위(요청·차감 행·내보내기 행)만 센다.
"""

from __future__ import annotations

from typing import Any, Protocol

from ...core.problems import ApiError

Row = dict[str, Any]


class UsageStore(Protocol):
    async def daily(self, client_id: str, days: int) -> list[Row]: ...
    async def monthly(self, client_id: str, months: int) -> list[Row]: ...
    async def export_monthly(self, client_id: str, months: int) -> list[Row]: ...


def plan_view(p: Any) -> dict[str, Any]:
    """플랜 한도 전부 (응답 이름). bulk = 대량 내보내기·변경분 피드 가능."""
    return {
        "planId": p.plan_id,
        "rpm": p.rpm,
        "dailyRows": p.daily_rows,
        "maxRangeMonths": p.max_range_months,
        "maxPageSize": p.max_page_size,
        "bulk": p.allow_bulk,
    }


def public_plans(plans: list[Any]) -> list[dict[str, Any]]:
    """공개 플랜만, 한도가 작은 것부터."""
    return [plan_view(p) for p in sorted((p for p in plans if p.public), key=lambda p: (p.daily_rows, p.rpm))]


def require_key(kind: str) -> None:
    if kind != "key":
        raise ApiError(401, "API_KEY_REQUIRED", "Unauthorized", "사용량 조회에는 API 키가 필요합니다.")


async def my_usage(store: UsageStore, *, client_id: str, plan: Any, rows_used_today: int, days: int) -> dict[str, Any]:
    rows = await store.daily(client_id, days)
    return {
        "clientId": client_id,
        "plan": plan.plan_id,
        "limits": plan_view(plan),
        # 이 키의 오늘(KST) — 하루 행 한도를 세는 단위와 같다
        "today": {"rowsUsed": rows_used_today, "rowsLeft": max(plan.daily_rows - rows_used_today, 0)},
        "items": [
            {
                "day": r["day"].isoformat(),
                "requests": r["requests"],
                "rows": r["rows"],
                "errors": r["errors"],
                "p95Ms": r["p95_ms"],
            }
            for r in rows
        ],
    }


async def monthly(store: UsageStore, client_id: str, months: int) -> dict[str, Any]:
    """달(KST)별 과금 대상 단위 — 이 고객의 모든 키 합. 사용량 기록 보관이 180일이라 최대 6개월."""
    usage = {r["month"]: r for r in await store.monthly(client_id, months)}
    exports = {r["month"]: r for r in await store.export_monthly(client_id, months)}
    items = []
    for m in sorted(usage.keys() | exports.keys(), reverse=True):
        u, e = usage.get(m, {}), exports.get(m, {})
        items.append(
            {
                "month": m,
                "requests": int(u.get("requests", 0)),
                "errors": int(u.get("errors", 0)),
                "rateLimited": int(u.get("rate_limited", 0)),
                "chargedRows": int(u.get("rows", 0)),
                "exports": int(e.get("jobs", 0)),
                "exportRows": int(e.get("rows", 0)),
            }
        )
    return {"clientId": client_id, "basis": "client", "items": items}
