"""관리 API (내부 리스너 :8611 에만 존재). admin 스코프 필수 + 모든 변경은 감사 로그."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

import orjson
from fastapi import APIRouter, Path, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from . import keys
from .auth import Principal, client_ip, forget_key
from .deps import require_scope
from .errors import ApiError
from .responses import OrjsonResponse
from .settings import settings

router = APIRouter(prefix="/v1/admin")


async def audit(
    c: AsyncConnection, request: Request, p: Principal, action: str, target: str, detail: dict | None = None
) -> None:
    """변경과 같은 트랜잭션(c)에서 기록한다 — 기록이 실패하면 변경도 되돌려져 '감사 기록 없는 변경'이 남지 않는다."""
    await c.execute(
        """INSERT INTO api.audit_log (actor, action, target, client_ip, detail)
                       VALUES (%s, %s, %s, %s, %s)""",
        (p.subject, action, target, client_ip(request, settings()), orjson.dumps(detail or {}).decode()),
    )


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    planId: Literal["free", "pro"] = "free"  # noqa: N815


@router.post("/clients", status_code=201)
async def create_client(request: Request, body: ClientIn, p: Principal = require_scope("admin")) -> OrjsonResponse:
    async with request.app.state.res.pg.connection() as c, c.transaction():
        row = await (
            await c.execute(
                "INSERT INTO api.client (name, plan_id) VALUES (%s, %s) RETURNING client_id, created_at",
                (body.name, body.planId),
            )
        ).fetchone()
        await audit(c, request, p, "client.create", str(row["client_id"]), {"plan": body.planId})
    return OrjsonResponse(
        {"clientId": str(row["client_id"]), "name": body.name, "planId": body.planId}, status_code=201
    )


class KeyIn(BaseModel):
    scopes: list[Literal["read", "bulk", "admin", "ops"]] = Field(default=["read"], min_length=1)
    expiresInDays: int = Field(default=90, ge=1, le=365)  # noqa: N815


@router.post("/clients/{clientId}/keys", status_code=201)
async def create_key(
    request: Request,
    clientId: Annotated[str, Path(pattern=r"^[0-9a-f-]{36}$")],  # noqa: N803
    body: KeyIn,
    p: Principal = require_scope("admin"),
) -> OrjsonResponse:
    issued = keys.issue(settings().api_key_pepper.get_secret_value())
    expires = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=body.expiresInDays)
    async with request.app.state.res.pg.connection() as c, c.transaction():
        exists = await (await c.execute("SELECT 1 FROM api.client WHERE client_id=%s", (clientId,))).fetchone()
        if not exists:
            raise ApiError(404, "CLIENT_NOT_FOUND", "Not Found")
        await c.execute(
            """INSERT INTO api.api_key (key_id, client_id, secret_hmac, scopes, expires_at)
                           VALUES (%s, %s, %s, %s, %s)""",
            (issued.key_id, clientId, issued.secret_hmac, sorted(set(body.scopes)), expires),
        )
        await audit(c, request, p, "key.create", issued.key_id, {"client": clientId, "scopes": body.scopes})
    return OrjsonResponse(
        {
            "keyId": issued.key_id,
            "apiKey": issued.api_key,
            "scopes": sorted(set(body.scopes)),
            "expiresAt": expires.isoformat(),
            "warning": "이 값은 다시 표시되지 않습니다.",
        },
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/keys/{keyId}")
async def revoke_key(
    request: Request,
    keyId: Annotated[str, Path(pattern=r"^[2-9A-HJ-NP-Z]{12}$")],  # noqa: N803
    p: Principal = require_scope("admin"),
) -> OrjsonResponse:
    async with request.app.state.res.pg.connection() as c, c.transaction():
        row = await (
            await c.execute(
                "UPDATE api.api_key SET revoked_at = now() WHERE key_id=%s AND revoked_at IS NULL RETURNING key_id",
                (keyId,),
            )
        ).fetchone()
        if row is None:
            raise ApiError(404, "KEY_NOT_FOUND", "Not Found", "없거나 이미 폐기된 키")
        await audit(c, request, p, "key.revoke", keyId)
    # 커밋된 뒤에 캐시를 비운다 (되돌려진 폐기로 캐시만 지워지는 일 없음)
    await request.app.state.res.redis.delete(f"al:key:{keyId}")
    forget_key(keyId)  # 캐시 즉시 무효화 (다른 워커의 프로세스 내 캐시는 최대 5초, Redis 캐시는 바로)
    return OrjsonResponse({"keyId": keyId, "revoked": True})


@router.post("/partitions/{sggCd}/{dealYm}/retry", status_code=202)
async def retry_partition(
    request: Request,
    sggCd: Annotated[str, Path(pattern=r"^\d{5}$")],  # noqa: N803
    dealYm: Annotated[str, Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],  # noqa: N803
    p: Principal = require_scope("admin"),
) -> OrjsonResponse:
    ym = dealYm.replace("-", "")
    async with request.app.state.res.pg.connection() as c, c.transaction():
        row = await (
            await c.execute(
                """UPDATE ops.ingest_partition SET status='RETRY', attempts=0, next_due_at=now(), last_error=NULL,
                      updated_at=now()
               WHERE sgg_cd=%s AND deal_ym=%s AND status IN ('QUARANTINED','RETRY') RETURNING status""",
                (sggCd, ym),
            )
        ).fetchone()
        if row is None:
            raise ApiError(
                409, "PARTITION_NOT_QUARANTINED", "Conflict", "격리·재시도 상태의 파티션만 재시도할 수 있습니다."
            )
        await audit(c, request, p, "partition.retry", f"{sggCd}/{ym}")
    return OrjsonResponse(
        {
            "partition": f"{sggCd}/{dealYm}",
            "status": "RETRY",
            "note": "due_partitions_sensor 가 다음 주기(≤5분)에 수집 실행을 요청합니다.",
        },
        status_code=202,
    )
