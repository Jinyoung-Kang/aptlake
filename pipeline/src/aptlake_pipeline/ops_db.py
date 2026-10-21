"""ops 스키마 (PostgreSQL) 접근: 파티션 상태 머신·품질 결과·데이터셋 버전.

상태 머신·발행 대기는 원천(source)별이다 (V013, ADR-050): 'rtms' = 아파트 매매, 'rent' = 전월세.
원천 이름은 호출 예산 이름과 같다. 인자를 생략하면 매매.
"""

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
from .rtms.fingerprint import UmdAliases, norm_text

MAX_ATTEMPTS = 3
TRADE = "rtms"
# 앞 실행이 수집 중(FETCHING)에 정리 없이 끝난 파티션의 재시도 사유 (다음 실행이 시작할 때 되돌린다)
INTERRUPTED = "interrupted: previous run ended while fetching"
# 실패한 실행이 되돌린 파티션을 다시 받기까지 — 센서가 죽은 실행의 FETCHING 을 다시 잡는 간격도 이 값(definitions._tick)
FAILED_RUN_RETRY_AFTER = dt.timedelta(hours=1)


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


def ensure_partitions(deal_ym: str, sgg_codes: Iterable[str], source: str = TRADE) -> None:
    with conn() as c:
        c.cursor().executemany(
            "INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(source, s, deal_ym) for s in sgg_codes],
        )


def ensure_grid(deal_yms: list[str], sgg_codes: list[str], source: str = TRADE) -> None:
    """월 × 시군구 격자를 한 문장으로 등록 (이미 있으면 그대로)."""
    with conn() as c:
        c.execute(
            """INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym)
               SELECT %s, s, m FROM unnest(%s::text[]) AS s CROSS JOIN unnest(%s::text[]) AS m
               ON CONFLICT DO NOTHING""",
            (source, sgg_codes, deal_yms),
        )


def partition_states(deal_ym: str, source: str = TRADE) -> dict[str, PartitionState]:
    with conn() as c:
        rows = c.execute(
            """SELECT sgg_cd, deal_ym, status, attempts, rows_last, payload_sha256, fetch_count
               FROM ops.ingest_partition WHERE source = %s AND deal_ym = %s""",
            (source, deal_ym),
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


def mark_fetching(deal_ym: str, sgg_cd: str, source: str = TRADE) -> None:
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='FETCHING', updated_at=now()
                     WHERE source=%s AND sgg_cd=%s AND deal_ym=%s""",
            (source, sgg_cd, deal_ym),
        )


def mark_unchanged(deal_ym: str, sgg_cd: str, fetched_at: dt.datetime, source: str = TRADE) -> None:
    """원본 해시가 마지막 적재본과 같음 → bronze 변화 없음. 관측 횟수만 올린다.
    상태는 반영 기록으로 정한다: 마지막 적재본(last_ingest_id)이 silver 에 반영됐으면(merged_ingest_id, V014) MERGED,
    아니면 LOADED(다음 반영 대상). 직전 상태값으로 정하면 반영 전 적재본이 수집 중 되돌리기(RETRY)·예산 소진·수집 실패를
    거친 뒤 같은 원본을 다시 받을 때 MERGED 로 넘어가, 그 적재본은 silver 에 들어가지 않고 발행 대기도 켜지지 않았다."""
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition
               SET status = CASE WHEN last_ingest_id IS DISTINCT FROM merged_ingest_id THEN 'LOADED' ELSE 'MERGED' END,
                   attempts = 0, last_error = NULL, last_fetched_at = %s,
                   fetch_count = fetch_count + 1, next_due_at = %s, updated_at = now()
               WHERE source=%s AND sgg_cd=%s AND deal_ym=%s""",
            (fetched_at, next_due(deal_ym, fetched_at), source, sgg_cd, deal_ym),
        )


def mark_loaded(
    deal_ym: str,
    sgg_cd: str,
    *,
    rows: int,
    sha: str,
    ingest_id: str,
    fetched_at: dt.datetime,
    schema_ver: int | None = None,
    source: str = TRADE,
) -> None:
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition
               SET status='LOADED', attempts=0, last_error=NULL,
                   rows_prev = rows_last, rows_last = %s, payload_sha256 = %s, last_ingest_id = %s,
                   last_fetched_at = %s, last_changed_at = %s, fetch_count = fetch_count + 1,
                   next_due_at = %s, schema_ver = %s, updated_at = now()
               WHERE source=%s AND sgg_cd=%s AND deal_ym=%s""",
            (
                rows,
                sha,
                ingest_id,
                fetched_at,
                fetched_at,
                next_due(deal_ym, fetched_at),
                schema_ver,
                source,
                sgg_cd,
                deal_ym,
            ),  # fmt: skip
        )


def mark_failed(deal_ym: str, sgg_cd: str, error: str, *, quarantine_now: bool = False, source: str = TRADE) -> str:
    """실패 시 attempts+1. 3회 실패 또는 검사 실패면 QUARANTINED (관리자 재시도 대기)."""
    with conn() as c:
        row = c.execute(
            """UPDATE ops.ingest_partition
               SET attempts = attempts + 1,
                   status = CASE WHEN %s OR attempts + 1 >= %s THEN 'QUARANTINED' ELSE 'RETRY' END,
                   last_error = left(%s, 500),
                   next_due_at = now() + make_interval(mins => 30 * (attempts + 1)),
                   updated_at = now()
               WHERE source=%s AND sgg_cd=%s AND deal_ym=%s RETURNING status""",
            (quarantine_now, MAX_ATTEMPTS, error, source, sgg_cd, deal_ym),
        ).fetchone()
    return row["status"] if row else "UNKNOWN"


def release_attempt(deal_ym: str, sgg_cd: str, source: str = TRADE) -> None:
    """예산 소진·원천 중단은 파티션 결함이 아니므로 attempts 를 되돌리고 RETRY 로 둔다."""
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET attempts = greatest(attempts - 1, 0), status='RETRY'
               WHERE source=%s AND sgg_cd=%s AND deal_ym=%s""",
            (source, sgg_cd, deal_ym),
        )


def reset_fetching(
    deal_ym: str, source: str = TRADE, reason: str = INTERRUPTED, retry_after: dt.timedelta | None = None
) -> int:
    """FETCHING 으로 남은 파티션을 RETRY 로 되돌리고 사유를 남긴다 (재실행 멱등) → 되돌린 수.
    실행 시작: 앞 실행이 정리 없이 끝난 흔적(프로세스 종료 등) — 기본 사유. 실행 실패: 그 실행이 직접 실패 사유로 부른다.
    사유 없이 RETRY 로만 두면 수집 상태 화면에 빈 사유의 재시도가 쌓였다 (2026-10-21 전월세 446곳 — bronze 적재 실패로 끝난
    두 실행이 남긴 FETCHING 을 다음 실행이 사유 없이 되돌렸고, 그 실행은 예산 상한에 걸려 다시 받지 못했다).
    재시도 횟수(attempts)는 올리지 않는다 — 파티션 결함이 아니다.
    retry_after: 실행 실패 경로만 — 다음 기한을 지금 + 이 간격으로 둔다. 백필·재확인 파티션은 기한이 이미 지나 있어 그대로
    두면 센서 다음 틱(5분)이 같은 달을 다시 받는다 — 적재 단계가 계속 실패하면(결정적 오류, 긴 MinIO 장애) attempts 0 인 채
    5분마다 원천 호출(예산)을 쓴다. 원래 기한이 더 뒤여도(수동 실행이 기한 전 파티션까지 받은 경우) 이 간격으로 — 받은 원천을
    적재하지 못했으니 원래 기한까지 미루지 않는다 (예전 'FETCHING 1시간' 규칙과 같은 동작). 없으면 기한은 그대로."""
    with conn() as c:
        return c.execute(
            """UPDATE ops.ingest_partition SET status='RETRY', last_error=left(%s, 500),
                     next_due_at=coalesce(now() + %s::interval, next_due_at), updated_at=now()
                     WHERE source=%s AND deal_ym=%s AND status='FETCHING'""",
            (reason, retry_after, source, deal_ym),
        ).rowcount


def loaded_partitions(deal_ym: str, source: str = TRADE) -> list[dict[str, Any]]:
    with conn() as c:
        return c.execute(
            """SELECT sgg_cd, last_ingest_id, fetch_count, rows_last, rows_prev, last_changed_at
               FROM ops.ingest_partition WHERE source=%s AND deal_ym=%s AND status='LOADED' ORDER BY sgg_cd""",
            (source, deal_ym),
        ).fetchall()


def fetch_counts(deal_ym: str, source: str = TRADE) -> dict[str, int]:
    """시군구별 관측 횟수 — 미관측 연속 횟수 판단 기준."""
    with conn() as c:
        rows = c.execute(
            "SELECT sgg_cd, fetch_count FROM ops.ingest_partition WHERE source=%s AND deal_ym=%s", (source, deal_ym)
        ).fetchall()
    return {r["sgg_cd"]: r["fetch_count"] for r in rows}


def mark_merged(deal_ym: str, sgg_codes: Iterable[str], source: str = TRADE) -> None:
    """silver 반영을 마친 파티션: 상태와 함께 반영한 적재본(merged_ingest_id)을 남긴다 — 같은 원본 재수집의 판단 근거."""
    with conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='MERGED', merged_ingest_id = last_ingest_id, updated_at=now()
               WHERE source=%s AND deal_ym=%s AND sgg_cd = ANY(%s) AND status='LOADED'""",
            (source, deal_ym, list(sgg_codes)),
        )


def mark_month_merged(deal_ym: str, silver_snapshot: int | None, source: str = TRADE) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.month_state (source, deal_ym, needs_publish, silver_snapshot, merged_at)
               VALUES (%s, %s, true, %s, now())
               ON CONFLICT (source, deal_ym) DO UPDATE SET needs_publish = true,
                 silver_snapshot = EXCLUDED.silver_snapshot, merged_at = EXCLUDED.merged_at""",
            (source, deal_ym, silver_snapshot),
        )


def mark_month_published(deal_ym: str, version: str, source: str = TRADE) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO ops.month_state (source, deal_ym, needs_publish, published_version, published_at)
               VALUES (%s, %s, false, %s, now())
               ON CONFLICT (source, deal_ym) DO UPDATE SET needs_publish = false,
                 published_version = EXCLUDED.published_version, published_at = EXCLUDED.published_at""",
            (source, deal_ym, version),
        )


def month_needs_publish(deal_ym: str, source: str = TRADE) -> bool:
    with conn() as c:
        row = c.execute(
            "SELECT needs_publish FROM ops.month_state WHERE source=%s AND deal_ym=%s", (source, deal_ym)
        ).fetchone()
    return bool(row and row["needs_publish"])


def months_needing_publish(source: str = TRADE) -> list[str]:
    with conn() as c:
        return [
            r["deal_ym"]
            for r in c.execute(
                "SELECT deal_ym FROM ops.month_state WHERE source=%s AND needs_publish ORDER BY deal_ym DESC",
                (source,),
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
    publish_id: str,
    files: list[Any],
    baseline: bool = False,
    source: str = "trade",
) -> None:
    """버전과 그 발행의 달 스냅숏 파일을 한 트랜잭션으로 (둘 중 하나만 남지 않게).
    source: 'trade'(매매 달·단지 차원·지수) | 'rent'(전월세 달) — 지연·계보는 매매 달 발행끼리만 비교한다."""
    with conn() as c, c.transaction():
        c.execute(
            """INSERT INTO ops.dataset_version (version, data_as_of, partitions, snapshots, row_counts, run_id, publish_id,
                   snapshot_baseline, source)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                version,
                data_as_of,
                partitions,
                json.dumps(snapshots),
                json.dumps(row_counts),
                run_id,
                publish_id,
                baseline,
                source,
            ),
        )
        for ym in partitions if files and not baseline else []:  # 달 발행: 수집→발행 지연 (ADR-046)
            c.execute(
                """INSERT INTO ops.publish_lag (publish_id, sgg_cd, deal_ym, fetched_at, published_at)
                   SELECT %(pid)s, p.sgg_cd, p.deal_ym, p.last_changed_at, now()
                   FROM ops.ingest_partition p
                   WHERE p.source = 'rtms' AND p.deal_ym = %(ym)s AND p.status = 'MERGED' AND p.last_changed_at > (
                       SELECT max(v.published_at) FROM ops.dataset_version v
                       WHERE v.source = 'trade' AND v.publish_id IS NOT NULL AND v.version <> %(ver)s
                         AND %(ym)s = ANY (v.partitions))""",
                {"pid": publish_id, "ym": ym, "ver": version},
            )
        for f in files:
            c.execute(
                """INSERT INTO ops.dataset_month_file (publish_id, deal_ym, object_key, rows, bytes, sha256)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (publish_id, f.deal_ym, f.object_key, f.rows, f.bytes, f.sha256),
            )


def data_as_of_by_source() -> dict[str, dt.datetime]:
    """발행 기록의 원천('trade'·'rent')별 데이터 기준 시각 — 그 원천 발행들의 기준 시각 중 가장 늦은 값.
    마지막 발행 하나의 값이 아니라 최댓값이라 다른 원천이 발행하거나 이른 기준으로 다시 발행해도 뒤로 가지 않는다."""
    with conn() as c:
        rows = c.execute("SELECT source, max(data_as_of) AS t FROM ops.dataset_version GROUP BY source").fetchall()
    return {r["source"]: r["t"] for r in rows}


def last_month_file(deal_ym: str) -> str | None:
    """그 달의 마지막으로 기록된 스냅숏 파일 — 변경분 비교 기준 (커밋된 발행만, ADR-045)."""
    with conn() as c:
        row = c.execute(
            """SELECT f.object_key FROM ops.dataset_month_file f JOIN ops.dataset_version v USING (publish_id)
               WHERE f.deal_ym = %s ORDER BY v.published_at DESC, v.version DESC LIMIT 1""",
            (deal_ym,),
        ).fetchone()
    return row["object_key"] if row else None


def snapshot_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(발행 기록, 달 파일 기록) — 보관 정리용."""
    with conn() as c:
        versions = c.execute(
            "SELECT version, published_at, publish_id::text AS publish_id, snapshot_baseline FROM ops.dataset_version"
        ).fetchall()
        files = c.execute(
            """SELECT publish_id::text AS publish_id, deal_ym, object_key, deleted_at
               FROM ops.dataset_month_file"""
        ).fetchall()
    return versions, files


def mark_month_files_deleted(rows: list[tuple[str, str]]) -> None:
    with conn() as c:
        for publish_id, deal_ym in rows:
            c.execute(
                "UPDATE ops.dataset_month_file SET deleted_at = now() WHERE publish_id = %s AND deal_ym = %s",
                (publish_id, deal_ym),
            )


def umd_aliases() -> UmdAliases:
    """법정동 이름 대응표: (시군구, 정규화한 새 이름) → 옛 이름. 지문·단지 키를 만들 때 쓴다 (fingerprint.key_umd)."""
    with conn() as c:
        rows = c.execute("SELECT sgg_cd, new_name, key_name FROM ops.umd_alias").fetchall()
    return {(r["sgg_cd"], norm_text(r["new_name"])): r["key_name"] for r in rows}


def add_umd_aliases(rows: Iterable[tuple[str, str, str]], rule: str, source: str, at: dt.datetime) -> int:
    """대응표는 더하기만 한다 — 지우거나 바꾸면 그 이름의 거래 지문이 다시 바뀐다(그러면 수집 검사가 막는다)."""
    vals = [(sgg, new, old, rule, source, at) for sgg, new, old in rows]
    if not vals:
        return 0
    with conn() as c:
        cur = c.cursor()
        cur.executemany(
            """INSERT INTO ops.umd_alias (sgg_cd, new_name, key_name, rule, source, derived_at)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sgg_cd, new_name) DO NOTHING""",
            vals,
        )
    return len(vals)
