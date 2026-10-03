"""관리: 클라이언트·키 발급/폐기, 격리 파티션 재시도 — 업무 규칙 (순수).

모든 변경은 감사 기록과 같은 트랜잭션에서 일어난다 (저장소 책임). 키 비밀값은 발급 응답에서 한 번만 보인다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol

from ...core import keys
from ...core.problems import ApiError

Row = dict[str, Any]


@dataclass(frozen=True)
class Actor:
    """감사 기록의 주체 — 키 주체와 요청 IP."""

    subject: str
    ip: str


class AdminStore(Protocol):
    async def create_client(self, name: str, plan_id: str, actor: Actor) -> Row: ...
    async def create_key(
        self,
        client_id: str,
        issued: keys.IssuedKey,
        scopes: list[str],
        expires: dt.datetime,
        actor: Actor,
        detail: dict,
    ) -> bool: ...
    async def revoke_key(self, key_id: str, actor: Actor) -> bool: ...
    async def retry_partition(self, sgg: str, ym: str, actor: Actor) -> bool: ...


class KeyCache(Protocol):
    async def forget(self, key_id: str) -> None: ...


async def create_client(store: AdminStore, name: str, plan_id: str, actor: Actor) -> dict:
    row = await store.create_client(name, plan_id, actor)
    return {"clientId": str(row["client_id"]), "name": name, "planId": plan_id}


async def create_key(
    store: AdminStore,
    client_id: str,
    scopes: list[str],
    expires_in_days: int,
    actor: Actor,
    *,
    pepper: str,
    now: dt.datetime,
) -> dict:
    issued = keys.issue(pepper)
    expires = now + dt.timedelta(days=expires_in_days)
    granted = sorted(set(scopes))
    if not await store.create_key(client_id, issued, granted, expires, actor, {"client": client_id, "scopes": scopes}):
        raise ApiError(404, "CLIENT_NOT_FOUND", "Not Found")
    return {
        "keyId": issued.key_id,
        "apiKey": issued.api_key,
        "scopes": granted,
        "expiresAt": expires.isoformat(),
        "warning": "이 값은 다시 표시되지 않습니다.",
    }


async def revoke_key(store: AdminStore, cache: KeyCache, key_id: str, actor: Actor) -> dict:
    if not await store.revoke_key(key_id, actor):
        raise ApiError(404, "KEY_NOT_FOUND", "Not Found", "없거나 이미 폐기된 키")
    # 커밋된 뒤에 캐시를 비운다 (되돌려진 폐기로 캐시만 지워지는 일 없음)
    await cache.forget(key_id)
    return {"keyId": key_id, "revoked": True}


async def retry_partition(store: AdminStore, sgg: str, deal_ym: str, actor: Actor) -> dict:
    ym = deal_ym.replace("-", "")
    if not await store.retry_partition(sgg, ym, actor):
        raise ApiError(
            409, "PARTITION_NOT_QUARANTINED", "Conflict", "격리·재시도 상태의 파티션만 재시도할 수 있습니다."
        )
    return {
        "partition": f"{sgg}/{deal_ym}",
        "status": "RETRY",
        "note": "due_partitions_sensor 가 다음 주기(≤5분)에 수집 실행을 요청합니다.",
    }
