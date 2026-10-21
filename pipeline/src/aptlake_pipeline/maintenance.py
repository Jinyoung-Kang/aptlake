"""Iceberg 테이블 유지보수 (FR-601): 작은 파일 압축 → 스냅샷 만료 → 고아 파일 정리.

보존 기간 7일은 Trino 의 기본 최소 보존 기간과 같다 → 최근 7일 안의 시간여행(재현)은 항상 가능.

압축(optimize)은 파티션마다 쓰기 핸들을 연다. Trino 는 한 문장에 파티션 100개까지만 허용해
(ICEBERG_TOO_MANY_OPEN_PARTITIONS), 파티션이 1.7만 개인 bronze(계약월 × 시군구)에서 주간 정리가 실패했다.
→ 파티션이 많은 표는 '파일이 2개 이상인 파티션'만, 앞 열 값 하나 + 마지막 열 목록(IN) 단위로 90개씩 나눠 압축한다.
   (Trino 는 optimize 의 WHERE 를 파티션 열 조건으로 완전히 내려보낼 수 있어야 해서 열 사이 OR 는 쓰지 않는다.)
연(year) 변환 파티션에 정렬 쓰기(sorted_by)인 전월세 silver(520만 행, 다섯 해 모두 대상)를 한 문장으로 압축하다 Trino 힙(1.1GB)이
넘쳐 Trino 가 다시 떴다(2026-10-21, 힙 덤프가 남지 않아 무엇이 힙을 차지했는지는 확인 못 함). → '파일이 2개 이상인 연도'마다
한 문장으로 나눠 한 번에 다시 쓰는 양을 한 해로 줄인다. 조건은 변환 경계에 맞춘 [YYYY-01-01, YYYY+1-01-01) 이라 Trino 가
파티션 조건으로 내려보낸다(EXPLAIN 의 constraint 로 확인). 한 해보다 잘게는 나눌 수 없다(변환 경계와 맞아야 한다).
한 표에서 Trino 가 다시 뜨면 남은 표가 연결 오류로 줄줄이 실패했다 → 실패 뒤에는 Trino 가 다시 받을 때까지 기다리고,
끝내 응답이 없으면 남은 표는 건너뛰었다고 적는다(실패한 문장은 다시 하지 않는다 — 같은 초과가 되풀이될 수 있다).
한 표가 실패해도 나머지 표의 스냅샷 만료·정리는 계속하고, 실패는 끝에서 모아 알린다.

정리 전에 멱등 보정 한 가지를 먼저 한다: 전월세 현재 행 전체 기준으로 단지 차원 바로잡기 — 없는 단지 더하기, 처음
관측일 낮추기, 매매 기록 없는 단지의 빈 건축년도·일련번호 채우기(silver.backfill_rent_complexes). 달 반영도 같은 일을
하지만 그 기능 전에 반영된 달은 빠져 있다 — 배포 뒤 make maintenance 한 번으로 따라잡고, 그 뒤로는 달 반영이 놓친
단지의 안전망이다 (보통 넣은 수·고친 수 0). 새로 쓴 파일은 바로 이어지는 압축 대상이 된다.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence

from .lake import catalog, execute

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
    # 전월세 (ADR-050)
    "bronze.rtms_rent_raw",
    "stage.apt_rent",
    "silver.apt_rent",
    "gold.rent_serving",
    "gold.rent_version",
    "gold.region_rent_month",
    "gold.region_rent_rollup_month",
]
RETENTION = "7d"
MAX_WRITERS = 90  # Trino iceberg.max-partitions-per-writer 기본값 100 보다 작게
OPTIMIZE = "EXECUTE optimize(file_size_threshold => '64MB')"
_SAFE = re.compile(r"[0-9A-Za-z_\-]+")  # 파티션 값은 문장에 직접 넣으므로 형식을 확인한다 (코드·연월만 옴)


_YEAR_PARTITION = re.compile(r"partitioning = ARRAY\['year\(([a-z_][a-z0-9_]*)\)'\]")


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


def year_filters(column: str, years: Sequence[int]) -> list[str]:
    """연 변환 파티션 값(Iceberg: 1970 년부터 센 연 수) → 연도마다 optimize WHERE 절 하나 (변환 경계와 맞는 날짜 구간)."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", column):
        raise ValueError(f"unexpected column {column!r}")
    out = []
    for n in sorted(set(years)):
        if not 0 <= n < 8000:
            raise ValueError(f"unexpected year partition {n!r}")
        y = 1970 + n
        out.append(f"{column} >= DATE '{y:04d}-01-01' AND {column} < DATE '{y + 1:04d}-01-01'")
    return out


def _stats(table: str) -> dict[str, int]:
    schema, name = table.split(".")
    files = execute(f'SELECT count(*) FROM lake.{schema}."{name}$files"')[0][0]
    snaps = execute(f'SELECT count(*) FROM lake.{schema}."{name}$snapshots"')[0][0]
    return {"files": int(files), "snapshots": int(snaps)}


def _ddl(table: str) -> str:
    schema, name = table.split(".")
    return str(execute(f"SHOW CREATE TABLE lake.{schema}.{name}")[0][0])


def _year_field(ddl: str) -> str | None:
    """파티션이 연 변환 하나(year(열))뿐이면 그 열 이름."""
    m = _YEAR_PARTITION.search(ddl)
    return m.group(1) if m else None


def _identity_fields(ddl: str) -> list[str] | None:
    """항등 파티션 열 이름 (year(…) 같은 변환 파티션이 섞였거나 파티션이 없으면 None)."""
    m = re.search(r"partitioning = ARRAY\[([^\]]*)\]", ddl)
    if not m:
        return None
    fields = [f.strip().strip("'") for f in m.group(1).split(",") if f.strip()]
    return fields if fields and all(re.fullmatch(r"[a-z_][a-z0-9_]*", f) for f in fields) else None


def _optimize(table: str) -> int:
    """압축 문장 수를 돌려준다."""
    schema, name = table.split(".")
    ddl = _ddl(table)
    year_col = _year_field(ddl)
    if year_col:  # 연 변환 파티션 — 연도마다 한 문장 (정렬 버퍼를 한 연도만큼만)
        parts = execute(f'SELECT partition.{year_col}_year FROM lake.{schema}."{name}$partitions" WHERE file_count > 1')
        years = year_filters(year_col, [int(row[0]) for row in parts])
        for where in years:
            execute(f"ALTER TABLE lake.{table} {OPTIMIZE} WHERE {where}")
        return len(years)
    n_parts = int(execute(f'SELECT count(*) FROM lake.{schema}."{name}$partitions"')[0][0])
    fields = _identity_fields(ddl) if n_parts > MAX_WRITERS else None
    if not fields:  # 파티션이 적거나(한 문장으로 충분) 그 밖의 변환 파티션인 표
        execute(f"ALTER TABLE lake.{table} {OPTIMIZE}")
        return 1
    cols = ", ".join(f"partition.{f}" for f in fields)
    parts = execute(f'SELECT {cols} FROM lake.{schema}."{name}$partitions" WHERE file_count > 1')
    filters = partition_filters(fields, [[str(v) for v in row] for row in parts])
    for where in filters:
        execute(f"ALTER TABLE lake.{table} {OPTIMIZE} WHERE {where}")
    return len(filters)


def _await_trino(attempts: int = 36, delay_s: float = 5.0, sleep: Callable[[float], None] | None = None) -> bool:
    """Trino 가 질의를 다시 받을 때까지 기다린다 (재시작은 수십 초 — 기동 확인 간격과 비슷하게 최대 약 3분)."""
    for i in range(attempts):
        try:
            execute("SELECT 1")
            return True
        except Exception:
            if i + 1 < attempts:
                (sleep or time.sleep)(delay_s)
    return False


def run() -> dict[str, dict[str, int | str]]:
    out: dict[str, dict[str, int | str]] = {}
    failed: dict[str, str] = {}
    try:  # 실패해도 표 정리는 계속한다 (끝에서 함께 알림)
        from .silver import backfill_rent_complexes

        out["rent_complexes"] = backfill_rent_complexes()
    except Exception as e:
        failed["rent_complexes"] = f"{type(e).__name__}: {e}"[:300]
    cat = catalog()
    trino_down = False
    for t in TABLES:
        if trino_down:
            out[t] = {"skipped": "trino unavailable"}
            continue
        if not cat.table_exists(t):  # 아직 첫 실행 전인 표(예: 배포 직후 전월세 gold) — 실패가 아니라 건너뜀
            out[t] = {"skipped": "not created yet"}
            continue
        try:
            before = _stats(t)
            statements = _optimize(t)
            execute(f"ALTER TABLE lake.{t} EXECUTE expire_snapshots(retention_threshold => '{RETENTION}')")
            execute(f"ALTER TABLE lake.{t} EXECUTE remove_orphan_files(retention_threshold => '{RETENTION}')")
            after = _stats(t)
        except Exception as e:  # 한 표의 실패가 나머지 표의 정리를 막지 않도록
            failed[t] = f"{type(e).__name__}: {e}"[:300]
            out[t] = {"error": failed[t]}
            trino_down = (
                not _await_trino()
            )  # 그 표가 Trino 를 쓰러뜨렸으면 다시 뜰 때까지 (남은 표가 줄줄이 실패하지 않게)
            if trino_down:
                failed["trino"] = f"unavailable after {t} — remaining tables skipped"
            continue
        out[t] = {
            "optimize_statements": statements,
            "files_before": before["files"],
            "files_after": after["files"],
            "snapshots_before": before["snapshots"],
            "snapshots_after": after["snapshots"],
        }
    try:  # 달별 스냅숏 파일 보관 정리 (ADR-044) — 레이크 정리와 같은 주기
        from . import snapshots

        out["snapshots"] = snapshots.cleanup()  # type: ignore[assignment]
    except Exception as e:
        failed["snapshots"] = f"{type(e).__name__}: {e}"[:300]
    if failed:
        raise MaintenanceError(f"{len(failed)}/{len(TABLES)} tables failed: {failed}")
    return out
