"""내보내기 작업자 — 내부 API 프로세스 안에서 돈다 (FR-502).

1분마다 멈춘 작업을 정리하고, 대기 작업을 하나씩 선점해 Parquet 로 써서 올린다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ...core.resources import Resources
from . import storage
from .repository import ExportRepository

log = logging.getLogger(__name__)


async def run_job(repo: ExportRepository, job: dict) -> None:
    sql, qp = storage.export_query(job["params"])
    key = f"{job['client_id']}/{job['job_id']}.parquet"
    rows = await asyncio.to_thread(storage.write_parquet_and_upload, sql, qp, key)
    await repo.mark_done(job["job_id"], rows, key)


async def worker(res: Resources, poll_s: float = 2.0) -> None:
    repo = ExportRepository(res.pg)
    last_sweep = 0.0
    while True:
        try:
            now = asyncio.get_running_loop().time()
            if now - last_sweep > 60:  # 1분마다 멈춘 작업 정리
                last_sweep = now
                if n := await repo.fail_stalled():
                    log.warning("marked %d stalled export jobs as failed", n)
            job = await repo.claim()
            if job is None:
                await asyncio.sleep(poll_s)
                continue
            try:
                await run_job(repo, job)
            except Exception as e:  # noqa: BLE001
                log.exception("export failed")
                await repo.mark_failed(job["job_id"], type(e).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("export worker loop error")
            await asyncio.sleep(poll_s)


def start(res: Resources) -> asyncio.Task:
    return asyncio.create_task(worker(res))


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
