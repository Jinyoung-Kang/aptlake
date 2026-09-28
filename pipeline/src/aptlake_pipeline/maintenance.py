"""Iceberg 테이블 유지보수 (FR-601): 작은 파일 압축 → 스냅샷 만료 → 고아 파일 정리.

보존 기간 7일은 Trino 의 기본 최소 보존 기간과 같다 → 최근 7일 안의 시간여행(재현)은 항상 가능.

압축(optimize)은 파티션마다 쓰기 핸들을 연다. Trino 는 한 문장에 파티션 100개까지만 허용해
(ICEBERG_TOO_MANY_OPEN_PARTITIONS), 파티션이 1.7만 개인 bronze(계약월 × 시군구)에서 주간 정리가 실패했다.
→ 파티션이 많은 표는 '파일이 2개 이상인 파티션'만, 앞 열 값 하나 + 마지막 열 목록(IN) 단위로 90개씩 나눠 압축한다.
   (Trino 는 optimize 의 WHERE 를 파티션 열 조건으로 완전히 내려보낼 수 있어야 해서 열 사이 OR 는 쓰지 않는다.)
한 표가 실패해도 나머지 표의 스냅샷 만료·정리는 계속하고, 실패는 끝에서 모아 알린다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from .lake import execute

TABLES = [
    "bronze.rtms_raw",
    # stage 는 월마다 덮어써 스냅샷이 가장 빨리 쌓인다 (정리 대상에서 빠져 있었음)
    "stage.apt_trade",
    "silver.apt_trade",
    "silver.apt_complex",
    "silver.region",
    "gold.trade_serving",
    "gold.region_month",
    "gold.region_rollup_month",
    "gold.trade_version",
    "gold.complex_summary",
    "gold.price_index",
    "gold.index_reference",
]
RETENTION = "7d"
MAX_WRITERS = 90  # Trino iceberg.max-partitions-per-writer 기본값 100 보다 작게
OPTIMIZE = "EXECUTE optimize(file_size_threshold => '64MB')"
_SAFE = re.compile(r"[0-9A-Za-z_\-]+")  # 파티션 값은 문장에 직접 넣으므로 형식을 확인한다 (코드·연월만 옴)


class MaintenanceError(RuntimeError):
    pass


def partition_filters(
    fields: Sequence[str], parts: Sequence[Sequence[str]], max_writers: int = MAX_WRITERS
) -> list[str]:
    """압축할 파티션 값 목록 → optimize WHERE 절 목록. 절 하나가 여는 파티션은 max_writers 이하.

    fields = 항등(identity) 파티션 열 이름, parts = 각 파티션의 값 (fields 순서).
    앞 열들의 값이 같은 것끼리 묶고 마지막 열은 IN 목록으로 — Trino 가 그대로 파티션 조건으로 내려보낼 수 있는 모양.
    """
    groups: dict[tuple[str, ...], list[str]] = {}
    for values in parts:
        if len(values) != len(fields) or not all(_SAFE.fullmatch(v) for v in values):
            raise ValueError(f"unexpected partition value {values!r}")
        groups.setdefault(tuple(values[:-1]), []).append(values[-1])
    out = []
    for head, lasts in sorted(groups.items()):
        lasts = sorted(set(lasts))
        prefix = "".join(f"{f} = '{v}' AND " for f, v in zip(fields[:-1], head, strict=True))
        for i in range(0, len(lasts), max_writers):
            chunk = ", ".join(f"'{v}'" for v in lasts[i : i + max_writers])
            out.append(f"{prefix}{fields[-1]} IN ({chunk})")
    return out


def _stats(table: str) -> dict[str, int]:
    schema, name = table.split(".")
    files = execute(f'SELECT count(*) FROM lake.{schema}."{name}$files"')[0][0]
    snaps = execute(f'SELECT count(*) FROM lake.{schema}."{name}$snapshots"')[0][0]
    return {"files": int(files), "snapshots": int(snaps)}


def _identity_fields(table: str) -> list[str] | None:
    """항등 파티션 열 이름 (year(…) 같은 변환 파티션이 섞였거나 파티션이 없으면 None)."""
    schema, name = table.split(".")
    ddl = execute(f"SHOW CREATE TABLE lake.{schema}.{name}")[0][0]
    m = re.search(r"partitioning = ARRAY\[([^\]]*)\]", ddl)
    if not m:
        return None
    fields = [f.strip().strip("'") for f in m.group(1).split(",") if f.strip()]
    return fields if fields and all(re.fullmatch(r"[a-z_][a-z0-9_]*", f) for f in fields) else None


def _optimize(table: str) -> int:
    """압축 문장 수를 돌려준다."""
    schema, name = table.split(".")
    n_parts = int(execute(f'SELECT count(*) FROM lake.{schema}."{name}$partitions"')[0][0])
    fields = _identity_fields(table) if n_parts > MAX_WRITERS else None
    if not fields:  # 파티션이 적거나(한 문장으로 충분) 변환 파티션인 표
        execute(f"ALTER TABLE lake.{table} {OPTIMIZE}")
        return 1
    cols = ", ".join(f"partition.{f}" for f in fields)
    parts = execute(f'SELECT {cols} FROM lake.{schema}."{name}$partitions" WHERE file_count > 1')
    filters = partition_filters(fields, [[str(v) for v in row] for row in parts])
    for where in filters:
        execute(f"ALTER TABLE lake.{table} {OPTIMIZE} WHERE {where}")
    return len(filters)


def run() -> dict[str, dict[str, int | str]]:
    out: dict[str, dict[str, int | str]] = {}
    failed: dict[str, str] = {}
    for t in TABLES:
        try:
            before = _stats(t)
            statements = _optimize(t)
            execute(f"ALTER TABLE lake.{t} EXECUTE expire_snapshots(retention_threshold => '{RETENTION}')")
            execute(f"ALTER TABLE lake.{t} EXECUTE remove_orphan_files(retention_threshold => '{RETENTION}')")
            after = _stats(t)
        except Exception as e:  # 한 표의 실패가 나머지 표의 정리를 막지 않도록
            failed[t] = f"{type(e).__name__}: {e}"[:300]
            out[t] = {"error": failed[t]}
            continue
        out[t] = {
            "optimize_statements": statements,
            "files_before": before["files"],
            "files_after": after["files"],
            "snapshots_before": before["snapshots"],
            "snapshots_after": after["snapshots"],
        }
    if failed:
        raise MaintenanceError(f"{len(failed)}/{len(TABLES)} tables failed: {failed}")
    return out
