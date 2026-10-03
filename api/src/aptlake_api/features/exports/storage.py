"""내보내기 파일 저장 (인프라): ClickHouse 에서 스트리밍으로 읽어 Parquet 로 쓰고 exports 버킷(1일 뒤 자동 삭제)에 올린다.

내려받기는 서명 URL (exports 계정 키로 서명, 브라우저가 닿는 공개 엔드포인트 기준).
"""

from __future__ import annotations

import datetime as dt
import tempfile
from typing import Any

import boto3
import clickhouse_connect
import orjson
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config

from ...core.settings import settings

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


def export_query(params: dict[str, Any] | str) -> tuple[str, dict[str, Any]]:
    """작업 매개변수 → 질의와 바인딩 값 (WHERE 는 고정 조각만 조립)."""
    p = params if isinstance(params, dict) else orjson.loads(params)
    where = ["deal_date BETWEEN {a:Date} AND {b:Date}"]
    qp: dict[str, Any] = {"a": dt.date.fromisoformat(p["from"]), "b": dt.date.fromisoformat(p["to"])}
    if p.get("sggCd"):
        where.append("sgg_cd = {s:String}")
        qp["s"] = p["sggCd"]
    if not p.get("includeCancelled"):
        where.append("is_cancelled = 0")
    sql = f"SELECT {', '.join(COLUMNS)} FROM trade_current WHERE {' AND '.join(where)} ORDER BY deal_date, trade_id"
    return sql, qp


def write_parquet_and_upload(sql: str, qp: dict, key: str) -> int:
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
