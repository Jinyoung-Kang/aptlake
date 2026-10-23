"""내보내기 파일 저장 (인프라): 달별 스냅숏 파일(snapshots 버킷, ADR-044)을 한 달씩 받아 걸러 Parquet 로 쓰고
exports 버킷(1일 뒤 자동 삭제)에 올린다. 늘 같은 경로라 같은 버전·조건이면 같은 행이다.

내려받기는 서명 URL (exports 계정 키로 서명, 브라우저가 닿는 공개 엔드포인트 기준).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import tempfile
from collections.abc import Callable
from typing import Any

import boto3
import orjson
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from botocore.config import Config

from ...core.settings import settings

# Arrow 기본 풀(mimalloc)은 스레드마다 힙을 쥐어서 최대 메모리가 코어 수를 따라 는다 — 16코어 556MB > 상한(ADR-056).
# api 에서 Arrow 를 쓰는 곳은 내보내기뿐이라 시스템 malloc 풀로 고정한다(코어 수와 무관하게 약 210MB).
pa.set_memory_pool(pa.system_memory_pool())

BUCKET = "exports"
SNAPSHOT_BUCKET = "snapshots"
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
# 내보내기 파일 형식 (달 스냅숏의 같은 열 형식 — pipeline snapshots.SCHEMA 와 맞는지 시험한다)
EXPORT_SCHEMA = pa.schema(
    [
        ("trade_id", pa.string()),
        ("sgg_cd", pa.string()),
        ("deal_date", pa.date32()),
        ("complex_key", pa.string()),
        ("apt_nm", pa.string()),
        ("umd_nm", pa.string()),
        ("jibun", pa.string()),
        ("area_m2", pa.decimal128(9, 4)),
        ("floor", pa.int16()),
        ("price_manwon", pa.uint32()),
        ("ppm2", pa.float64()),
        ("is_cancelled", pa.uint8()),
        ("cancel_date", pa.date32()),
        ("registered_date", pa.date32()),
        ("apt_dong", pa.string()),
        ("deal_kind", pa.string()),
        ("seller_type", pa.string()),
        ("buyer_type", pa.string()),
        ("build_year", pa.uint16()),
        ("is_outlier", pa.uint8()),
        ("version", pa.uint16()),
    ]
)


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


def filter_month(table: pa.Table, params: dict[str, Any]) -> pa.Table:
    """달 스냅숏 하나에서 조건(기간·시군구·해제 포함 여부)에 맞는 행만, 내보내기 열 순서로. 순수 — 파일은 이미
    (deal_date, trade_id) 순이라 다시 정렬하지 않는다."""
    a, b = dt.date.fromisoformat(params["from"]), dt.date.fromisoformat(params["to"])
    mask = pc.and_(pc.greater_equal(table["deal_date"], pa.scalar(a)), pc.less_equal(table["deal_date"], pa.scalar(b)))
    if params.get("sggCd"):
        mask = pc.and_(mask, pc.equal(table["sgg_cd"], params["sggCd"]))
    if not params.get("includeCancelled"):
        mask = pc.and_(mask, pc.equal(table["is_cancelled"], 0))
    return table.filter(mask).select(COLUMNS).cast(EXPORT_SCHEMA)


def _download(object_key: str, path: str) -> None:
    _s3(settings().s3_endpoint).download_file(SNAPSHOT_BUCKET, object_key, path)


def write_export(
    files: list[dict[str, Any]],
    params: dict[str, Any] | str,
    out_path: str,
    fetch: Callable[[str, str], None] = _download,
) -> tuple[int, str]:
    """달 파일을 하나씩 받아 걸러 이어 쓴다 (한 번에 한 달만 메모리에). (행 수, 결과 sha256). 스레드에서 실행.
    달 파일을 받은 뒤 기록된 해시와 맞는지 확인한다 — 다른 내용이면 같은 버전이라 할 수 없다."""
    p = params if isinstance(params, dict) else orjson.loads(params)
    rows = 0
    with tempfile.TemporaryDirectory() as d, pq.ParquetWriter(out_path, EXPORT_SCHEMA, compression="zstd") as w:
        for f in files:
            src = os.path.join(d, "month.parquet")
            fetch(f["object_key"], src)
            if f.get("sha256") and _sha256(src) != f["sha256"]:
                raise SnapshotIntegrityError(f["object_key"])
            part = filter_month(pq.read_table(src), p)
            w.write_table(part)
            rows += part.num_rows
            os.remove(src)
    return rows, _sha256(out_path)


def export_and_upload(files: list[dict[str, Any]], params: dict[str, Any] | str, key: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "export.parquet")
        rows, digest = write_export(files, params, out)
        _s3(settings().s3_endpoint).upload_file(out, BUCKET, key)
    return rows, digest


class SnapshotIntegrityError(Exception):
    pass


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
