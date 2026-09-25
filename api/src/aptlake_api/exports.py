"""대량 내려받기 작업자 (FR-502 exports).

- 작업 선점은 `FOR UPDATE SKIP LOCKED` → 작업자를 여러 개 띄워도 한 작업은 한 번만 처리
- ClickHouse(api_reader)에서 스트리밍으로 읽어 Parquet 파일로 쓰고, exports 버킷(1일 뒤 자동 삭제)에 올린다
- 내려받기는 15분짜리 서명 URL (exports 계정 키로 서명, 브라우저가 닿는 공개 엔드포인트 기준)
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import tempfile

import boto3
import clickhouse_connect
import orjson
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config

from .resources import Resources
from .settings import settings

log = logging.getLogger(__name__)
BUCKET = "exports"
COLUMNS = [
    "trade_id",
    "sgg_cd",
    "deal_date",
    "complex_key",
    "apt_nm",
    "umd_nm",
    "jibun",
    "area_m2",
    "floor",
    "price_manwon",
    "ppm2",
    "is_cancelled",
    "cancel_date",
    "registered_date",
    "apt_dong",
    "deal_kind",
    "seller_type",
    "buyer_type",
    "build_year",
    "is_outlier",
    "version",
]


def _s3(endpoint: str):
    s = settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.s3_export_access_key,
        aws_secret_access_key=s.s3_export_secret_key.get_secret_value(),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
    )


def presign(object_key: str) -> str:
    return _s3(settings().s3_public_endpoint).generate_presigned_url(
        "get_object", Params={"Bucket": BUCKET, "Key": object_key}, ExpiresIn=settings().export_url_ttl_s
    )


async def _claim(res: Resources) -> dict | None:
    async with res.pg.connection() as c, c.transaction():
        return await (  # type: ignore[return-value]  # dict_row 은 풀에서 지정
            await c.execute(
                """UPDATE api.export_job SET status='running', started_at=now()
               WHERE job_id = (SELECT job_id FROM api.export_job WHERE status='queued'
                               ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
               RETURNING job_id, client_id, params"""
            )
        ).fetchone()


async def _run_job(res: Resources, job: dict) -> None:
    params = job["params"] if isinstance(job["params"], dict) else orjson.loads(job["params"])
    where = ["deal_date BETWEEN {a:Date} AND {b:Date}"]
    qp = {"a": dt.date.fromisoformat(params["from"]), "b": dt.date.fromisoformat(params["to"])}
    if params.get("sggCd"):
        where.append("sgg_cd = {s:String}")
        qp["s"] = params["sggCd"]
    if not params.get("includeCancelled"):
        where.append("is_cancelled = 0")
    sql = f"SELECT {', '.join(COLUMNS)} FROM trade_current WHERE {' AND '.join(where)} ORDER BY deal_date, trade_id"
    key = f"{job['client_id']}/{job['job_id']}.parquet"
    rows = await asyncio.to_thread(_write_parquet_and_upload, sql, qp, key)
    async with res.pg.connection() as c:
        await c.execute(
            """UPDATE api.export_job SET status='done', rows=%s, object_key=%s, finished_at=now()
                           WHERE job_id=%s""",
            (rows, key, job["job_id"]),
        )


def _write_parquet_and_upload(sql: str, qp: dict, key: str) -> int:
    """블록 단위 스트리밍으로 Parquet 작성 → 업로드 (전체 결과를 메모리에 올리지 않음). 스레드에서 실행."""
    s = settings()
    client = clickhouse_connect.get_client(
        host=s.ch_host,
        port=s.ch_port,
        username="exporter",
        password=s.ch_export_password.get_secret_value(),
        database="aptlake",
    )
    rows = 0
    try:
        with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
            writer = None
            with client.query_arrow_stream(sql, parameters=qp) as stream:
                for batch in stream:
                    tbl = pa.Table.from_batches([batch]) if isinstance(batch, pa.RecordBatch) else batch
                    if writer is None:
                        writer = pq.ParquetWriter(f.name, tbl.schema, compression="zstd")
                    writer.write_table(tbl)
                    rows += tbl.num_rows
            if writer:
                writer.close()
            _s3(s.s3_endpoint).upload_file(f.name, BUCKET, key)
    finally:
        client.close()
    return rows


async def worker(res: Resources, poll_s: float = 2.0) -> None:
    while True:
        try:
            job = await _claim(res)
            if job is None:
                await asyncio.sleep(poll_s)
                continue
            try:
                await _run_job(res, job)
            except Exception as e:  # noqa: BLE001
                log.exception("export failed")
                async with res.pg.connection() as c:
                    await c.execute(
                        """UPDATE api.export_job SET status='failed', error=%s, finished_at=now()
                                       WHERE job_id=%s""",
                        (type(e).__name__, job["job_id"]),
                    )
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
