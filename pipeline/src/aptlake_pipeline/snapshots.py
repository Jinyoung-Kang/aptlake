"""달별 스냅숏 파일 (ADR-044, 데이터 제품 설계 계획서 1장).

달을 발행할 때마다 그 달의 서빙 내용(ClickHouse trade_current 스테이징 — 곧 교체될 바로 그 행)을 Parquet 로 남기고,
버전 기록과 같은 트랜잭션으로 ops.dataset_month_file 에 등록한다(publish.commit_version).
버전 V 의 데이터 = 달마다, V 이전에 기록된 가장 최근 파일. 서빙(ClickHouse)은 최신만, Iceberg 스냅숏은 7~14일만
남아서, 과거 발행 버전을 그대로 다시 내려면 이 파일이 필요하다.

- 파일 키: snapshots/trade/deal_ym=YYYYMM/{publish_id}.parquet — 발행 시도마다 다르다(실패한 시도의 파일은 기록되지
  않아 보이지 않고, 정리 작업이 지운다).
- 쓰기는 Python(Arrow 스트림 → pyarrow → S3). ClickHouse s3() 함수로 쓰면 자격증명이 질의 문자열(query_log)에 남는다.
- 보관 정책(설정): 최근 KEEP_DAYS 일의 모든 버전 + 달(KST)마다 마지막 버전 KEEP_MONTHLY_DAYS 일 + 최신 버전.
  실측(2026-10): 70개월 전체 129MB, 한 달 최대 2.7MB, 달 발행 하루 3~4회.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.config import Config as BotoConfig

from .config import settings

BUCKET = "snapshots"
KEEP_DAYS = 30
KEEP_MONTHLY_DAYS = 365
KST = dt.timezone(dt.timedelta(hours=9))
_YM = re.compile(r"\d{6}")

# trade_current 의 모든 열, 같은 순서. 파일마다 형식이 같도록 Arrow 형식을 고정한다
SCHEMA = pa.schema(
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
        ("valid_from", pa.timestamp("ms", tz="UTC")),
        ("missing_since", pa.timestamp("ms", tz="UTC")),
    ]
)
COLUMNS = SCHEMA.names


@dataclass(frozen=True)
class MonthFile:
    deal_ym: str
    object_key: str
    rows: int
    bytes: int
    sha256: str


def object_key(deal_ym: str, publish_id: str) -> str:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    return f"trade/deal_ym={deal_ym}/{publish_id}.parquet"


def month_query(table: str, deal_ym: str) -> str:
    if table not in ("trade_current", "trade_current_staging") or not _YM.fullmatch(deal_ym):
        raise ValueError((table, deal_ym))
    return (
        f"SELECT {', '.join(COLUMNS)} FROM aptlake.{table} WHERE toYYYYMM(deal_date) = {deal_ym} "
        "ORDER BY deal_date, trade_id"
    )


def write_parquet(batches: Iterable[pa.RecordBatch | pa.Table], path: str) -> int:
    """Arrow 블록을 고정 형식으로 바꿔 Parquet(zstd)로 쓴다. 0행이어도 형식이 있는 파일을 남긴다."""
    rows = 0
    with pq.ParquetWriter(path, SCHEMA, compression="zstd") as w:
        for b in batches:
            t = pa.Table.from_batches([b]) if isinstance(b, pa.RecordBatch) else b
            t = t.select(COLUMNS).cast(SCHEMA)
            w.write_table(t)
            rows += t.num_rows
    return rows


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class SnapshotStore:
    """snapshots 버킷 — snapshot 계정(Put·Get·Delete·List, 이 버킷만)."""

    def __init__(self) -> None:
        s = settings()
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_snapshot_access_key,
            aws_secret_access_key=str(s.s3_snapshot_secret_key),
            region_name="us-east-1",
            config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def put(self, path: str, key: str) -> None:
        self.s3.upload_file(path, BUCKET, key)

    def download(self, key: str, path: str) -> None:
        self.s3.download_file(BUCKET, key, path)

    def read(self, key: str) -> pa.Table:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "m.parquet")
            self.download(key, path)
            return pq.read_table(path)

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=BUCKET, Key=key)


def write_month(
    client: Any, table: str, deal_ym: str, publish_id: str, store: SnapshotStore
) -> tuple[MonthFile, pa.Table]:
    """ClickHouse 표(스테이징 또는 서빙)의 한 달을 파일로 써서 올린다. (파일 기록, 쓴 내용) — 내용은 변경분 비교에 쓴다
    (한 달 최대 6만 행 수준이라 메모리에 둔다)."""
    key = object_key(deal_ym, publish_id)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "m.parquet")
        with client.query_arrow_stream(month_query(table, deal_ym)) as stream:
            rows = write_parquet(stream, path)
        size, digest = os.path.getsize(path), sha256_file(path)
        store.put(path, key)
        table_read = pq.read_table(path)
    return MonthFile(deal_ym, key, rows, size, digest), table_read


# ───────────────────────── 버전 → 파일 (순수 — API 의 같은 규칙과 같은 시험 사례표를 쓴다) ─────────────────────────


def _order(v: Mapping[str, Any]) -> tuple:
    return (v["published_at"], v["version"])


def record_start(versions: Sequence[Mapping[str, Any]]) -> tuple | None:
    """기록 시작 = 가장 이른 기준선 버전. 그 전 버전은 일부 달만 파일이 있어 '그 버전 그대로'가 아니다."""
    starts = [_order(v) for v in versions if v.get("snapshot_baseline")]
    return min(starts) if starts else None


def files_for_version(
    versions: Sequence[Mapping[str, Any]], files: Sequence[Mapping[str, Any]], target: str
) -> list[Mapping[str, Any]] | None:
    """버전 target 시점의 달 파일들(달 순). 기록 없는 버전이면 None. 기록 시작(기준선) 전 버전이면 [].

    versions: version·published_at·publish_id / files: publish_id·deal_ym·… (지운 파일 포함 — 판단은 호출한 쪽)
    """
    by_ver = {v["version"]: v for v in versions}
    t = by_ver.get(target)
    if t is None:
        return None
    start = record_start(versions)
    if start is None or _order(t) < start:
        return []
    pub = {str(v["publish_id"]): v for v in versions if v.get("publish_id")}
    best: dict[str, tuple[tuple, Mapping[str, Any]]] = {}
    for f in files:
        v = pub.get(str(f["publish_id"]))
        if v is None or _order(v) > _order(t) or _order(v) < start:  # 기준선 전 발행의 파일은 쓰지 않는다
            continue
        if f["deal_ym"] not in best or _order(v) > best[f["deal_ym"]][0]:
            best[f["deal_ym"]] = (_order(v), f)
    return [best[ym][1] for ym in sorted(best)]


def kept_versions(versions: Sequence[Mapping[str, Any]], now: dt.datetime) -> set[str]:
    """보관 정책에 남는 버전: 최근 KEEP_DAYS 일 전부 + 달(KST)마다 마지막 버전 KEEP_MONTHLY_DAYS 일 + 최신."""
    start = record_start(versions)
    vs = sorted((v for v in versions if v.get("publish_id") and start is not None and _order(v) >= start), key=_order)
    if not vs:
        return set()
    keep = {vs[-1]["version"]}
    last_of_month: dict[str, Mapping[str, Any]] = {}
    for v in vs:
        at = v["published_at"]
        if at >= now - dt.timedelta(days=KEEP_DAYS):
            keep.add(v["version"])
        last_of_month[at.astimezone(KST).strftime("%Y%m")] = v
    for v in last_of_month.values():
        if v["published_at"] >= now - dt.timedelta(days=KEEP_MONTHLY_DAYS):
            keep.add(v["version"])
    return keep


def files_to_delete(
    versions: Sequence[Mapping[str, Any]], files: Sequence[Mapping[str, Any]], now: dt.datetime
) -> list[Mapping[str, Any]]:
    """남는 버전 어디에서도 쓰지 않는, 아직 지우지 않은 파일. 기록에 없는 발행(실패한 시도)의 파일은 여기 없다 —
    그런 파일은 기록이 없어 목록에도 없고, 버킷 목록과 대조해 따로 지운다(orphan_keys)."""
    used: set[tuple[str, str]] = set()
    for ver in kept_versions(versions, now):
        for f in files_for_version(versions, files, ver) or []:
            used.add((str(f["publish_id"]), f["deal_ym"]))
    return [f for f in files if not f.get("deleted_at") and (str(f["publish_id"]), f["deal_ym"]) not in used]


def orphan_keys(bucket_keys: Iterable[tuple[str, dt.datetime]], files: Sequence[Mapping[str, Any]],
                now: dt.datetime, grace: dt.timedelta = dt.timedelta(days=1)) -> list[str]:  # fmt: skip
    """버킷에는 있는데 기록이 없는 파일(발행 도중 실패) — 진행 중인 발행의 파일을 지우지 않게 하루 지난 것만."""
    known = {f["object_key"] for f in files}
    return [k for k, at in bucket_keys if k.startswith("trade/") and k not in known and at < now - grace]


def cleanup(now: dt.datetime | None = None, store: SnapshotStore | None = None) -> dict[str, int]:
    """보관 정책 밖 파일과 기록 없는 파일(실패한 발행)을 지운다. 기록 행은 남기고 지운 시각만 채운다 (주간 정리 작업)."""
    from . import ops_db

    now = now or dt.datetime.now(tz=dt.UTC)
    store = store or SnapshotStore()
    versions, files = ops_db.snapshot_records()
    doomed = files_to_delete(versions, files, now)
    for f in doomed:
        store.delete(f["object_key"])
    ops_db.mark_month_files_deleted([(str(f["publish_id"]), f["deal_ym"]) for f in doomed])
    keys: list[tuple[str, dt.datetime]] = []
    for page in store.s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="trade/"):
        keys += [(o["Key"], o["LastModified"]) for o in page.get("Contents", [])]
    orphans = orphan_keys(keys, files, now)
    for k in orphans:
        store.delete(k)
    return {"deleted": len(doomed), "orphans": len(orphans), "kept_files": len(files) - len(doomed)}
