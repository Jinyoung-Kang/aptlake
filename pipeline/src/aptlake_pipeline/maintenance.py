"""Iceberg 테이블 유지보수 (FR-601): 작은 파일 압축 → 스냅샷 만료 → 고아 파일 정리.

보존 기간 7일은 Trino 의 기본 최소 보존 기간과 같다 → 최근 7일 안의 시간여행(재현)은 항상 가능.
"""

from __future__ import annotations

from .lake import execute

TABLES = [
    "bronze.rtms_raw",
    "silver.apt_trade",
    "silver.apt_complex",
    "gold.trade_serving",
    "gold.region_month",
    "gold.trade_version",
    "gold.complex_summary",
    "gold.price_index",
]
RETENTION = "7d"


def _stats(table: str) -> dict[str, int]:
    schema, name = table.split(".")
    files = execute(f'SELECT count(*) FROM lake.{schema}."{name}$files"')[0][0]
    snaps = execute(f'SELECT count(*) FROM lake.{schema}."{name}$snapshots"')[0][0]
    return {"files": int(files), "snapshots": int(snaps)}


def run() -> dict[str, dict[str, int]]:
    out = {}
    for t in TABLES:
        before = _stats(t)
        execute(f"ALTER TABLE lake.{t} EXECUTE optimize(file_size_threshold => '64MB')")
        execute(f"ALTER TABLE lake.{t} EXECUTE expire_snapshots(retention_threshold => '{RETENTION}')")
        execute(f"ALTER TABLE lake.{t} EXECUTE remove_orphan_files(retention_threshold => '{RETENTION}')")
        after = _stats(t)
        out[t] = {
            "files_before": before["files"],
            "files_after": after["files"],
            "snapshots_before": before["snapshots"],
            "snapshots_after": after["snapshots"],
        }
    return out
