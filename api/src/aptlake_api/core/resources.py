"""프로세스 수명 자원: PostgreSQL 풀, Redis, ClickHouse(읽기 전용 / 사용량 쓰기 전용 사용자 분리)."""

from __future__ import annotations

from dataclasses import dataclass

import clickhouse_connect
import redis.asyncio as aioredis
from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .settings import Settings


@dataclass
class Resources:
    pg: AsyncConnectionPool
    redis: aioredis.Redis
    ch: AsyncClient  # api_reader: readonly=1, 시간·메모리 상한
    ch_usage: AsyncClient  # usage_writer: usage_event INSERT 만


async def open_resources(s: Settings) -> Resources:
    pg = AsyncConnectionPool(
        s.pg_dsn.get_secret_value(),
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
    )
    await pg.open(wait=True, timeout=30)
    # 연결이 모자라면 오류 대신 잠시 대기 (부하 측정에서 MaxConnectionsError 로 500 이 나던 문제)
    pool = aioredis.BlockingConnectionPool.from_url(
        s.redis_url.get_secret_value(),
        decode_responses=True,
        max_connections=64,
        timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )
    r = aioredis.Redis(connection_pool=pool)
    ch = await clickhouse_connect.get_async_client(
        host=s.ch_host,
        port=s.ch_port,
        username="api_reader",
        password=s.ch_reader_password.get_secret_value(),
        database="aptlake",
        autogenerate_session_id=False,
        compress=False,
        # 응답 없는 서버를 기본 300초까지 기다리지 않게 — 서버 쪽 질의 상한(max_execution_time 5초) + 여유 (QA-007)
        connect_timeout=3,
        send_receive_timeout=10,
    )
    ch_usage = await clickhouse_connect.get_async_client(
        host=s.ch_host,
        port=s.ch_port,
        username="usage_writer",
        password=s.ch_usage_password.get_secret_value(),
        database="aptlake",
        autogenerate_session_id=False,
        connect_timeout=3,
        send_receive_timeout=10,
    )
    return Resources(pg, r, ch, ch_usage)


async def close_resources(res: Resources) -> None:
    await res.pg.close()
    await res.redis.aclose()
    await res.ch.close()
    await res.ch_usage.close()
