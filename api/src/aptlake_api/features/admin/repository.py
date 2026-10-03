"""관리 데이터 접근 (운영 DB). 변경마다 감사 기록을 같은 트랜잭션에 남긴다."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

import orjson
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

from ...core import keys
from ...core.auth import forget_key
from .service import Actor

Row = dict[str, Any]


async def audit(c: AsyncConnection, actor: Actor, action: str, target: str, detail: dict | None = None) -> None:
    """변경과 같은 트랜잭션(c)에서 기록한다 — 기록이 실패하면 변경도 되돌려져 '감사 기록 없는 변경'이 남지 않는다."""
    await c.execute(
        """INSERT INTO api.audit_log (actor, action, target, client_ip, detail)
                       VALUES (%s, %s, %s, %s, %s)""",
        (actor.subject, action, target, actor.ip, orjson.dumps(detail or {}).decode()),
    )


class AdminRepository:
    def __init__(self, pg: AsyncConnectionPool):
        self.pg = pg

    async def create_client(self, name: str, plan_id: str, actor: Actor) -> Row:
        async with self.pg.connection() as c, c.transaction():
            row = cast(
                Row,
                await (
                    await c.execute(
                        "INSERT INTO api.client (name, plan_id) VALUES (%s, %s) RETURNING client_id, created_at",
                        (name, plan_id),
                    )
                ).fetchone(),
            )
            await audit(c, actor, "client.create", str(row["client_id"]), {"plan": plan_id})
        return row

    async def create_key(
        self,
        client_id: str,
        issued: keys.IssuedKey,
        scopes: list[str],
        expires: dt.datetime,
        actor: Actor,
        detail: dict,
    ) -> bool:
        async with self.pg.connection() as c, c.transaction():
            exists = await (await c.execute("SELECT 1 FROM api.client WHERE client_id=%s", (client_id,))).fetchone()
            if not exists:
                return False
            await c.execute(
                """INSERT INTO api.api_key (key_id, client_id, secret_hmac, scopes, expires_at)
                               VALUES (%s, %s, %s, %s, %s)""",
                (issued.key_id, client_id, issued.secret_hmac, scopes, expires),
            )
            await audit(c, actor, "key.create", issued.key_id, detail)
        return True

    async def revoke_key(self, key_id: str, actor: Actor) -> bool:
        async with self.pg.connection() as c, c.transaction():
            row = await (
                await c.execute(
                    "UPDATE api.api_key SET revoked_at = now() WHERE key_id=%s AND revoked_at IS NULL RETURNING key_id",
                    (key_id,),
                )
            ).fetchone()
            if row is None:
                return False
            await audit(c, actor, "key.revoke", key_id)
        return True

    async def retry_partition(self, sgg: str, ym: str, actor: Actor) -> bool:
        async with self.pg.connection() as c, c.transaction():
            row = await (
                await c.execute(
                    """UPDATE ops.ingest_partition SET status='RETRY', attempts=0, next_due_at=now(), last_error=NULL,
                          updated_at=now()
                   WHERE sgg_cd=%s AND deal_ym=%s AND status IN ('QUARANTINED','RETRY') RETURNING status""",
                    (sgg, ym),
                )
            ).fetchone()
            if row is None:
                return False
            await audit(c, actor, "partition.retry", f"{sgg}/{ym}")
        return True


class KeyCacheRepository:
    """키 조회 캐시 (Redis 공유 캐시 + 프로세스 내 캐시)."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def forget(self, key_id: str) -> None:
        await self.redis.delete(f"al:key:{key_id}")
        forget_key(key_id)  # 캐시 즉시 무효화 (다른 워커의 프로세스 내 캐시는 최대 5초, Redis 캐시는 바로)
