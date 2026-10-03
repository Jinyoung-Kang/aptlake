"""내 사용량 — 업무 규칙 (순수). 조회 대상은 항상 인증된 키의 클라이언트 (요청으로 다른 클라이언트를 지정할 수 없음)."""

from __future__ import annotations

from typing import Any, Protocol

from ...core.problems import ApiError

Row = dict[str, Any]


class UsageStore(Protocol):
    async def daily(self, client_id: str, days: int) -> list[Row]: ...


def require_key(kind: str) -> None:
    if kind != "key":
        raise ApiError(401, "API_KEY_REQUIRED", "Unauthorized", "사용량 조회에는 API 키가 필요합니다.")


async def my_usage(
    store: UsageStore, *, client_id: str, plan_id: str, rpm: int, daily_rows: int, days: int
) -> dict[str, Any]:
    rows = await store.daily(client_id, days)
    return {
        "clientId": client_id,
        "plan": plan_id,
        "limits": {"rpm": rpm, "dailyRows": daily_rows},
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
