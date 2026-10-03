"""내보내기 작업 데이터 접근 (운영 DB PostgreSQL api.export_job)."""

from __future__ import annotations

from typing import Any, cast

from psycopg_pool import AsyncConnectionPool

Row = dict[str, Any]
STALE_AFTER = "30 minutes"  # 이보다 오래 running 이면 작업자가 도중에 죽은 것 (정상 작업은 수 분 안에 끝남)


class ExportRepository:
    def __init__(self, pg: AsyncConnectionPool):
        self.pg = pg

    async def create_if_room(self, client_id: str, key_id: str, params_json: str, max_running: int) -> str | None:
        """한도 안이면 작업을 만들고 ID, 넘으면 None. '확인 후 추가'를 클라이언트별 잠금 안에서 —
        동시에 여러 요청이 와도 진행 중 작업이 한도를 넘지 않게."""
        async with self.pg.connection() as c, c.transaction():
            await c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"export:{client_id}",))
            running = cast(
                Row,
                await (
                    await c.execute(
                        "SELECT count(*) AS n FROM api.export_job WHERE client_id=%s AND status IN ('queued','running')",
                        (client_id,),
                    )
                ).fetchone(),
            )
            if running["n"] >= max_running:
                return None
            row = cast(
                Row,
                await (
                    await c.execute(
                        "INSERT INTO api.export_job (client_id, key_id, params) VALUES (%s, %s, %s) RETURNING job_id",
                        (client_id, key_id, params_json),
                    )
                ).fetchone(),
            )
        return str(row["job_id"])

    async def get(self, job_id: str, client_id: str) -> Row | None:
        async with self.pg.connection() as c:
            return cast(
                Row | None,
                await (
                    await c.execute(
                        """SELECT job_id, status, rows, error, object_key, created_at, finished_at FROM api.export_job
                           WHERE job_id = %s AND client_id = %s""",
                        (job_id, client_id),
                    )
                ).fetchone(),
            )

    # ───── 작업자 ─────

    async def fail_stalled(self) -> int:
        """작업자(내부 API 프로세스)가 작업 도중 재시작되면 그 작업은 running 으로 영원히 남는다.
        클라이언트당 동시 한도에 계속 잡혀 내보내기를 영영 못 하게 되므로 실패로 정리한다."""
        async with self.pg.connection() as c:
            cur = await c.execute(
                f"""UPDATE api.export_job SET status='failed', error='stalled (worker restarted)', finished_at=now()
                   WHERE status='running' AND started_at < now() - interval '{STALE_AFTER}'"""
            )
            return cur.rowcount

    async def claim(self) -> Row | None:
        """대기 작업 하나를 선점 (FOR UPDATE SKIP LOCKED — 작업자가 여럿이어도 한 작업은 한 번만)."""
        async with self.pg.connection() as c, c.transaction():
            return cast(
                Row | None,
                await (
                    await c.execute(
                        """UPDATE api.export_job SET status='running', started_at=now()
                       WHERE job_id = (SELECT job_id FROM api.export_job WHERE status='queued'
                                       ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                       RETURNING job_id, client_id, params"""
                    )
                ).fetchone(),
            )

    async def mark_done(self, job_id: Any, rows: int, object_key: str) -> None:
        async with self.pg.connection() as c:
            await c.execute(
                "UPDATE api.export_job SET status='done', rows=%s, object_key=%s, finished_at=now() WHERE job_id=%s",
                (rows, object_key, job_id),
            )

    async def mark_failed(self, job_id: Any, error: str) -> None:
        async with self.pg.connection() as c:
            await c.execute(
                "UPDATE api.export_job SET status='failed', error=%s, finished_at=now() WHERE job_id=%s",
                (error, job_id),
            )
