"""ops 스키마 (PostgreSQL) 접근: 파티션 상태 머신·품질 결과·데이터셋 버전."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings

MAX_ATTEMPTS = 3


@contextmanager
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(settings().pg_dsn, row_factory=dict_row) as c:
        yield c


@dataclass(frozen=True)
class PartitionState:
    sgg_cd: str
    deal_ym: str
    status: str
    attempts: int
    rows_last: int | None
    payload_sha256: str | None
    fetch_count: int


def leaf_regions() -> list[str]:
    with conn() as c:
        rows = c.execute("SELECT sgg_cd FROM ops.region WHERE is_leaf AND active ORDER BY sgg_cd").fetchall()
    return [r["sgg_cd"] for r in rows]


def ensure_partitions(deal_ym: str, sgg_codes: Iterable[str]) -> None:
    with conn() as c:
        c.cursor().executemany(
            "INSERT INTO ops.ingest_partition (sgg_cd, deal_ym) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(s, deal_ym) for s in sgg_codes],
        )


def ensure_grid(deal_yms: list[str], sgg_codes: list[str]) -> None:
    """월 × 시군구 격자를 한 문장으로 등록 (이미 있으면 그대로)."""
    with conn() as c:
        c.execute(
            """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym)
               SELECT s, m FROM unnest(%s::text[]) AS s CROSS JOIN unnest(%s::text[]) AS m
               ON CONFLICT DO NOTHING""",
            (sgg_codes, deal_yms),
        )


def partition_states(deal_ym: str) -> dict[str, PartitionState]:
    with conn() as c:
        rows = c.execute(
            """SELECT sgg_cd, deal_ym, status, attempts, rows_last, payload_sha256, fetch_count
               FROM ops.ingest_partition WHERE deal_ym = %s""",
            (deal_ym,),
        ).fetchall()
    return {
        r["sgg_cd"]: PartitionState(
            r["sgg_cd"], r["deal_ym"], r["status"], r["attempts"], r["rows_last"], r["payload_sha256"], r["fetch_count"]
        )
        for r in rows
    }


def next_due(deal_ym: str, now: dt.datetime) -> dt.datetime:
    """재확인 주기 (기획서 6-3 제안): 최근 3개월 매일, 12개월 이내 매월, 그 이전 분기."""
    y, m = int(deal_ym[:4]), int(deal_ym[4:])
    age = (now.year - y) * 12 + (now.month - m)
    if age < 3:
        return now + dt.timedelta(days=1)
    if age < 12:
        return now + dt.timedelta(days=30)
    return now + dt.timedelta(days=90)


def mark_fetching(deal_ym: str, sgg_cd: str) -> None:
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='FETCHING', updated_at=now()
                     WHERE sgg_cd=%s AND deal_ym=%s""",
            (sgg_cd, deal_ym),
        )


def mark_unchanged(deal_ym: str, sgg_cd: str, fetched_at: dt.datetime, prev_status: str) -> None:
    """원본 해시가 마지막 적재본과 같음 → bronze·silver 변화 없음. 관측 횟수만 올린다.
    아직 silver 에 반영 전(LOADED)이면 그대로 두고, 그 외엔 MERGED 로 돌린다."""
    status = "LOADED" if prev_status == "LOADED" else "MERGED"
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition
               SET status = %s, attempts = 0, last_error = NULL, last_fetched_at = %s,
                   fetch_count = fetch_count + 1, next_due_at = %s, updated_at = now()
               WHERE sgg_cd=%s AND deal_ym=%s""",
            (status, fetched_at, next_due(deal_ym, fetched_at), sgg_cd, deal_ym),
        )


def mark_loaded(deal_ym: str, sgg_cd: str, *, rows: int, sha: str, ingest_id: str, fetched_at: dt.datetime) -> None:
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition
               SET status='LOADED', attempts=0, last_error=NULL,
                   rows_prev = rows_last, rows_last = %s, payload_sha256 = %s, last_ingest_id = %s,
                   last_fetched_at = %s, last_changed_at = %s, fetch_count = fetch_count + 1,
                   next_due_at = %s, updated_at = now()
               WHERE sgg_cd=%s AND deal_ym=%s""",
            (rows, sha, ingest_id, fetched_at, fetched_at, next_due(deal_ym, fetched_at), sgg_cd, deal_ym),
        )


def mark_failed(deal_ym: str, sgg_cd: str, error: str, *, quarantine_now: bool = False) -> str:
    """실패 시 attempts+1. 3회 실패 또는 검사 실패면 QUARANTINED (관리자 재시도 대기)."""
    with conn() as c:
        row = c.execute(
            """UPDATE ops.ingest_partition
               SET attempts = attempts + 1,
                   status = CASE WHEN %s OR attempts + 1 >= %s THEN 'QUARANTINED' ELSE 'RETRY' END,
                   last_error = left(%s, 500),
                   next_due_at = now() + make_interval(mins => 30 * (attempts + 1)),
                   updated_at = now()
               WHERE sgg_cd=%s AND deal_ym=%s RETURNING status""",
            (quarantine_now, MAX_ATTEMPTS, error, sgg_cd, deal_ym),
        ).fetchone()
    return row["status"] if row else "UNKNOWN"


def reset_fetching(deal_ym: str) -> None:
    """중단된 실행이 남긴 FETCHING 을 RETRY 로 되돌린다 (재실행 멱등)."""
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='RETRY', updated_at=now()
                     WHERE deal_ym=%s AND status='FETCHING'""",
            (deal_ym,),
        )


def loaded_partitions(deal_ym: str) -> list[dict[str, Any]]:
    with conn() as c:
        return c.execute(
            """SELECT sgg_cd, last_ingest_id, fetch_count, rows_last, rows_prev
               FROM ops.ingest_partition WHERE deal_ym=%s AND status='LOADED' ORDER BY sgg_cd""",
            (deal_ym,),
        ).fetchall()


def mark_merged(deal_ym: str, sgg_codes: Iterable[str]) -> None:
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='MERGED', updated_at=now()
               WHERE deal_ym=%s AND sgg_cd = ANY(%s) AND status='LOADED'""",
            (deal_ym, list(sgg_codes)),
        )


def mark_month_merged(deal_ym: str, silver_snapshot: int | None) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.month_state (deal_ym, needs_publish, silver_snapshot, merged_at)
               VALUES (%s, true, %s, now())
               ON CONFLICT (deal_ym) DO UPDATE SET needs_publish = true,
                 silver_snapshot = EXCLUDED.silver_snapshot, merged_at = EXCLUDED.merged_at""",
            (deal_ym, silver_snapshot),
        )


def mark_month_published(deal_ym: str, version: str) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.month_state (deal_ym, needs_publish, published_version, published_at)
               VALUES (%s, false, %s, now())
               ON CONFLICT (deal_ym) DO UPDATE SET needs_publish = false,
                 published_version = EXCLUDED.published_version, published_at = EXCLUDED.published_at""",
            (deal_ym, version),
        )


def month_needs_publish(deal_ym: str) -> bool:
    with conn() as c:
        row = c.execute("SELECT needs_publish FROM ops.month_state WHERE deal_ym=%s", (deal_ym,)).fetchone()
    return bool(row and row["needs_publish"])


def months_needing_publish() -> list[str]:
    with conn() as c:
        return [
            r["deal_ym"]
            for r in c.execute(
                "SELECT deal_ym FROM ops.month_state WHERE needs_publish ORDER BY deal_ym DESC"
            ).fetchall()
        ]


def record_dq(
    asset: str,
    partition: str | None,
    check_name: str,
    passed: bool,
    *,
    severity: str = "ERROR",
    blocking: bool = False,
    metric: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.dq_result (asset, partition, check_name, passed, severity, blocking, metric, run_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                asset,
                partition,
                check_name,
                passed,
                severity,
                blocking,
                json.dumps(metric or {}, default=str, ensure_ascii=False),
                run_id,
            ),
        )


def record_dataset_version(
    version: str,
    data_as_of: dt.datetime,
    partitions: list[str],
    snapshots: dict[str, Any],
    row_counts: dict[str, int],
    run_id: str | None,
) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.dataset_version (version, data_as_of, partitions, snapshots, row_counts, run_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (version, data_as_of, partitions, json.dumps(snapshots), json.dumps(row_counts), run_id),
        )
