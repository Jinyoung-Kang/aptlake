"""관리 API (내부 리스너 :8611 에만 존재, HTTP 계층). admin 스코프 필수 + 모든 변경은 감사 로그."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field

from ...core.auth import Principal, client_ip
from ...core.http import require_scope
from ...core.params import YM_Q
from ...core.responses import OrjsonResponse
from ...core.settings import settings
from . import service
from .repository import AdminRepository, KeyCacheRepository

router = APIRouter(prefix="/v1/admin")


def _actor(request: Request, p: Principal) -> service.Actor:
    return service.Actor(p.subject, client_ip(request, settings()))


def _repo(request: Request) -> AdminRepository:
    return AdminRepository(request.app.state.res.pg)


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    planId: Literal["free", "pro"] = "free"  # noqa: N815


@router.post("/clients", status_code=201)
async def create_client(request: Request, body: ClientIn, p: Principal = require_scope("admin")) -> OrjsonResponse:
    out = await service.create_client(_repo(request), body.name, body.planId, _actor(request, p))
    return OrjsonResponse(out, status_code=201)


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
    out = await service.create_key(
        _repo(request),
        clientId,
        list(body.scopes),
        body.expiresInDays,
        _actor(request, p),
        pepper=settings().api_key_pepper.get_secret_value(),
        now=dt.datetime.now(tz=dt.UTC),
    )
    return OrjsonResponse(out, status_code=201, headers={"Cache-Control": "no-store"})


@router.delete("/keys/{keyId}")
async def revoke_key(
    request: Request,
    keyId: Annotated[str, Path(pattern=r"^[2-9A-HJ-NP-Z]{12}$")],  # noqa: N803
    p: Principal = require_scope("admin"),
) -> OrjsonResponse:
    cache = KeyCacheRepository(request.app.state.res.redis)
    return OrjsonResponse(await service.revoke_key(_repo(request), cache, keyId, _actor(request, p)))


@router.post("/partitions/{sggCd}/{dealYm}/retry", status_code=202)
async def retry_partition(
    request: Request,
    sggCd: Annotated[str, Path(pattern=r"^[0-9]{5}$")],  # noqa: N803
    dealYm: Annotated[str, Path(pattern=YM_Q)],  # noqa: N803
    p: Principal = require_scope("admin"),
) -> OrjsonResponse:
    out = await service.retry_partition(_repo(request), sggCd, dealYm, _actor(request, p))
    return OrjsonResponse(out, status_code=202)
