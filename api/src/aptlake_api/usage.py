"""사용량 계측 (FR-504): 요청 1건 = usage_event 1건.

요청 경로에서는 메모리 큐에 넣기만 하고(논블로킹), 백그라운드 태스크가 1초 또는 1,000건마다
ClickHouse 에 배치 INSERT 한다 → API 지연 영향 최소화 (기획서 10장 목표 p95 영향 < 2ms).
큐가 가득 차면(ClickHouse 장애) 이벤트를 버리고 카운터를 올린다 — API 가용성이 계측보다 우선.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging

from clickhouse_connect.driver.asyncclient import AsyncClient
from prometheus_client import Counter

log = logging.getLogger(__name__)
DROPPED = Counter("aptlake_usage_events_dropped_total", "usage events dropped (buffer full or insert failure)")
COLUMNS = ["at", "key_id", "client_id", "plan_id", "route", "status", "rows", "latency_ms", "trace_id"]


class UsageRecorder:
    def __init__(self, ch: AsyncClient, interval: float = 1.0, max_batch: int = 1000, max_queue: int = 50_000):
        self.ch, self.interval, self.max_batch = ch, interval, max_batch
        self.q: asyncio.Queue[list] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task | None = None

    def record(
        self,
        *,
        key_id: str,
        client_id: str,
        plan_id: str,
        route: str,
        status: int,
        rows: int,
        latency_ms: int,
        trace_id: str,
    ) -> None:
        try:
            self.q.put_nowait(
                [dt.datetime.now(tz=dt.UTC), key_id, client_id, plan_id, route, status, rows, latency_ms, trace_id]
            )
        except asyncio.QueueFull:
            DROPPED.inc()

    async def _flush(self, batch: list[list]) -> None:
        try:
            await self.ch.insert("usage_event", batch, column_names=COLUMNS)
        except Exception:  # noqa: BLE001 — 계측 실패가 API 를 멈추면 안 된다
            DROPPED.inc(len(batch))
            log.exception("usage flush failed")

    async def run(self) -> None:
        while True:
            batch: list[list] = []
            deadline = asyncio.get_running_loop().time() + self.interval
            while len(batch) < self.max_batch:
                timeout = deadline - asyncio.get_running_loop().time()
                if timeout <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(self.q.get(), timeout))
                except TimeoutError:
                    break
            if batch:
                await self._flush(batch)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        rest = []
        while not self.q.empty():
            rest.append(self.q.get_nowait())
        if rest:
            await self._flush(rest)
