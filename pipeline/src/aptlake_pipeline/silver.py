"""Silver 정합 (FR-201~205): bronze → 타입 변환·지문 → stage → 한 번의 MERGE (SCD2).

SCD2 를 MERGE 한 문장으로 처리한다 (기획서 U-4 결정 = 한 번).
Trino MERGE 는 대상 행 하나에 원천 행 하나만 매칭되고 'NOT MATCHED BY SOURCE' 가 없으므로,
원천을 세 갈래로 만든다:
  UPSERT  : 이번 수집의 모든 행 (매칭 키 = 자기 키)
            · 현재행과 속성 같음  → last_seen 갱신
            · 현재행과 속성 다름  → 현재행 닫기 (is_current=false, valid_to)
            · 현재행 없음          → 신규 거래 INSERT (version 1)
  NEWVER  : 속성이 바뀐 행을 매칭 키 NULL 로 한 번 더 → 반드시 NOT MATCHED → 새 버전 INSERT
  MISSING : 이번 수집에 없는 현재행 → 삭제하지 않고 missing_since 표시 (FR-204)
한 문장 = 한 Iceberg 스냅샷이므로 '닫기'와 '새 버전'이 따로 보이는 중간 상태가 없다.
같은 stage 로 다시 실행하면 모든 행이 '속성 같음'으로 매칭돼 결과가 변하지 않는다 (NFR-01 멱등).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

import pyarrow as pa

from . import ops_db
from .lake import catalog, eq, execute, isin
from .rtms.fingerprint import assign_keys, display_text
from .rtms.parse import to_typed

STAGE = "stage.apt_trade"
MISSING_WARN_OBSERVATIONS = 3

STAGE_SCHEMA = pa.schema(
    [
        ("trade_key", pa.string()),
        ("dup_seq", pa.int32()),
        ("trade_id", pa.string()),
        ("attr_hash", pa.string()),
        ("complex_key", pa.string()),
        ("sgg_cd", pa.string()),
        ("deal_ym", pa.string()),
        ("umd_nm", pa.string()),
        ("apt_nm", pa.string()),
        ("jibun", pa.string()),
        ("deal_date", pa.date32()),
        ("price_manwon", pa.int64()),
        ("area_m2", pa.decimal128(9, 4)),
        ("floor", pa.int32()),
        ("build_year", pa.int32()),
        ("is_cancelled", pa.bool_()),
        ("cancel_date", pa.date32()),
        ("registered_date", pa.date32()),
        ("apt_dong", pa.string()),
        ("deal_kind", pa.string()),
        ("agent_sgg_nm", pa.string()),
        ("seller_type", pa.string()),
        ("buyer_type", pa.string()),
        ("land_leasehold", pa.bool_()),
        ("ingest_id", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
        ("fetch_seq", pa.int32()),
    ]
)

DATA_COLS = [f.name for f in STAGE_SCHEMA if f.name not in ("ingest_id", "ingested_at", "fetch_seq")]
_SGG = re.compile(r"\d{5}")
_YM = re.compile(r"\d{6}")
_IID = re.compile(r"[0-9A-Za-z\-T]{10,60}")


@dataclass
class MergeSummary:
    deal_ym: str
    partitions: list[str] = field(default_factory=list)
    staged_rows: int = 0
    dup_groups: int = 0
    silver_snapshot: int | None = None
    checks: list[dict] = field(default_factory=list)


def _stage_rows(deal_ym: str, parts: list[dict]) -> list[dict]:
    by_iid = {p["last_ingest_id"]: p for p in parts}
    bronze = catalog().load_table("bronze.rtms_raw")
    arrow = bronze.scan(
        row_filter=eq("deal_ym", deal_ym) & isin("ingest_id", set(by_iid)),
        selected_fields=("ingest_id", "sgg_cd", "fetched_at", "row_json", "row_ord"),
    ).to_arrow()
    grouped: dict[str, list] = defaultdict(list)
    meta: dict[str, tuple[str, dt.datetime]] = {}
    for rec in arrow.sort_by("row_ord").to_pylist():
        grouped[rec["sgg_cd"]].append(to_typed(json.loads(rec["row_json"])))
        meta[rec["sgg_cd"]] = (rec["ingest_id"], rec["fetched_at"])
    rows: list[dict] = []
    for sgg, trades in grouped.items():
        iid, fetched_at = meta[sgg]
        fetch_seq = by_iid[iid]["fetch_count"]
        for k in assign_keys(trades):
            t = k.trade
            rows.append(
                {
                    "trade_key": k.trade_key,
                    "dup_seq": k.dup_seq,
                    "trade_id": k.trade_id,
                    "attr_hash": k.attr_hash,
                    "complex_key": k.complex_key,
                    "sgg_cd": t.sgg_cd,
                    "deal_ym": deal_ym,
                    "umd_nm": display_text(t.umd_nm),
                    "apt_nm": display_text(t.apt_nm),
                    "jibun": t.jibun,
                    "deal_date": t.deal_date,
                    "price_manwon": t.price_manwon,
                    "area_m2": t.area_m2,
                    "floor": t.floor,
                    "build_year": t.build_year,
                    "is_cancelled": t.is_cancelled,
                    "cancel_date": t.cancel_date,
                    "registered_date": t.registered_date,
                    "apt_dong": t.apt_dong,
                    "deal_kind": t.deal_kind,
                    "agent_sgg_nm": t.agent_sgg_nm,
                    "seller_type": t.seller_type,
                    "buyer_type": t.buyer_type,
                    "land_leasehold": t.land_leasehold,
                    "ingest_id": iid,
                    "ingested_at": fetched_at,
                    "fetch_seq": fetch_seq,
                }
            )
    return rows


def sgg_in_list(codes: Iterable[str]) -> str:
    """SQL IN 목록. 시군구 코드는 원천(행안부 API)에서 온 값이라 문장에 넣기 전에 경계에서 형식을 확인한다."""
    out = []
    for code in codes:
        if not _SGG.fullmatch(code):
            raise ValueError(f"unexpected sgg code {code!r}")
        out.append(f"'{code}'")
    return ", ".join(out)


def _parts_values(parts: list[dict], ingested: dict[str, dt.datetime]) -> str:
    """(sgg_cd, ingest_id, ingested_at, fetch_seq) VALUES 목록. 모든 값은 형식 검증 후 리터럴로 넣는다."""
    vals = []
    for p in parts:
        sgg, iid = p["sgg_cd"], p["last_ingest_id"]
        if not (_SGG.fullmatch(sgg) and _IID.fullmatch(iid)):
            raise ValueError(f"unexpected partition identifiers: {sgg!r} {iid!r}")
        ts = ingested[sgg].astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        vals.append(f"('{sgg}', '{iid}', TIMESTAMP '{ts} UTC', {int(p['fetch_count'])})")
    return ", ".join(vals)


def merge_sql(deal_ym: str, parts_values: str) -> str:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    cols = ", ".join(DATA_COLS)
    null_cols = ", ".join(
        f"CAST(NULL AS {t}) AS {c}" for c, t in _trino_types().items() if c not in ("trade_key", "dup_seq")
    )
    insert_cols = (
        f"{cols}, version, valid_from, valid_to, is_current, ingest_id, last_seen_ingest, "
        "last_seen_at, missing_since, missing_fetch_seq"
    )
    insert_vals = (
        ", ".join(f"src.{c}" for c in DATA_COLS)
        + ", src.new_version, src.ingested_at, NULL, true, src.ingest_id, src.ingest_id, "
        "src.ingested_at, NULL, NULL"
    )
    return f"""
MERGE INTO lake.silver.apt_trade t
USING (
    WITH p (sgg_cd, ingest_id, ingested_at, fetch_seq) AS (VALUES {parts_values}),
    s AS (
        SELECT st.* FROM lake.stage.apt_trade st
        WHERE st.deal_ym = '{deal_ym}' AND st.sgg_cd IN (SELECT sgg_cd FROM p)
    ),
    cur AS (
        SELECT trade_key, dup_seq, sgg_cd, attr_hash, version FROM lake.silver.apt_trade
        WHERE is_current AND deal_ym = '{deal_ym}' AND sgg_cd IN (SELECT sgg_cd FROM p)
    )
    SELECT s.trade_key AS mk, s.dup_seq AS ms, 'UPSERT' AS action, 1 AS new_version, s.*
    FROM s
    UNION ALL
    SELECT NULL, NULL, 'NEWVER', c.version + 1, s.*
    FROM s JOIN cur c ON c.trade_key = s.trade_key AND c.dup_seq = s.dup_seq
    WHERE c.attr_hash <> s.attr_hash
    UNION ALL
    SELECT c.trade_key, c.dup_seq, 'MISSING', NULL, c.trade_key, c.dup_seq, {null_cols},
           p.ingest_id, p.ingested_at, p.fetch_seq
    FROM cur c
    JOIN p ON p.sgg_cd = c.sgg_cd
    LEFT JOIN s ON s.trade_key = c.trade_key AND s.dup_seq = c.dup_seq
    WHERE s.trade_key IS NULL
) src
ON t.trade_key = src.mk AND t.dup_seq = src.ms AND t.is_current AND t.deal_ym = '{deal_ym}'
WHEN MATCHED AND src.action = 'MISSING' THEN UPDATE SET
    missing_since = coalesce(t.missing_since, src.ingested_at),
    missing_fetch_seq = coalesce(t.missing_fetch_seq, src.fetch_seq)
WHEN MATCHED AND t.attr_hash <> src.attr_hash THEN UPDATE SET
    is_current = false, valid_to = src.ingested_at
WHEN MATCHED THEN UPDATE SET
    last_seen_ingest = src.ingest_id, last_seen_at = src.ingested_at,
    missing_since = NULL, missing_fetch_seq = NULL
WHEN NOT MATCHED AND src.action <> 'MISSING' THEN INSERT ({insert_cols})
    VALUES ({insert_vals})
"""


def _trino_types() -> dict[str, str]:
    m = {
        pa.string(): "varchar",
        pa.int32(): "integer",
        pa.int64(): "bigint",
        pa.date32(): "date",
        pa.bool_(): "boolean",
        pa.decimal128(9, 4): "decimal(9,4)",
    }
    return {f.name: m[f.type] for f in STAGE_SCHEMA if f.name in DATA_COLS}


COMPLEX_MERGE = """
MERGE INTO lake.silver.apt_complex t
USING (
    SELECT complex_key, min(sgg_cd) AS sgg_cd, min(umd_nm) AS umd_nm, min(jibun) AS jibun, min(apt_nm) AS apt_nm,
           max(build_year) AS build_year, bool_or(land_leasehold) AS land_leasehold, min(deal_date) AS first_seen
    FROM lake.stage.apt_trade WHERE deal_ym = '{deal_ym}'
    GROUP BY complex_key
) s
ON t.complex_key = s.complex_key
WHEN MATCHED AND ((t.build_year IS NULL AND s.build_year IS NOT NULL) OR s.first_seen < t.first_seen) THEN UPDATE SET
    build_year = coalesce(t.build_year, s.build_year), first_seen = least(t.first_seen, s.first_seen)
WHEN NOT MATCHED THEN INSERT (complex_key, sgg_cd, umd_nm, jibun, apt_nm, build_year, land_leasehold, first_seen)
    VALUES (s.complex_key, s.sgg_cd, s.umd_nm, s.jibun, s.apt_nm, s.build_year, s.land_leasehold, s.first_seen)
"""


def _cancel_ratio(deal_ym: str) -> float | None:
    rows = execute(f"""SELECT count_if(is_cancelled), count(*) FROM lake.silver.apt_trade
                       WHERE is_current AND deal_ym = '{deal_ym}'""")
    cancelled, total = rows[0] if rows else (0, 0)
    return None if not total else cancelled / total


def merge_month(deal_ym: str, run_id: str | None = None) -> MergeSummary:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    summary = MergeSummary(deal_ym)
    parts = ops_db.loaded_partitions(deal_ym)
    if not parts:
        return summary
    rows = _stage_rows(deal_ym, parts)
    summary.partitions = [p["sgg_cd"] for p in parts]
    summary.staged_rows = len(rows)
    summary.dup_groups = sum(1 for r in rows if r["dup_seq"] == 1)

    ingested = {r["sgg_cd"]: r["ingested_at"] for r in rows}
    for p in parts:  # 0건 파티션도 '사라진 거래' 판정에 참여해야 한다
        ingested.setdefault(p["sgg_cd"], dt.datetime.now(tz=dt.UTC))

    stage = catalog().load_table(STAGE)
    arrow = pa.Table.from_pylist(rows, schema=STAGE_SCHEMA)
    stage.overwrite(arrow, overwrite_filter=eq("deal_ym", deal_ym))

    before = _cancel_ratio(deal_ym)
    execute(merge_sql(deal_ym, _parts_values(parts, ingested)))
    execute(COMPLEX_MERGE.format(deal_ym=deal_ym))
    after = _cancel_ratio(deal_ym)
    snap = catalog().load_table("silver.apt_trade").current_snapshot()
    summary.silver_snapshot = snap.snapshot_id if snap else None

    summary.checks = post_merge_checks(deal_ym, parts, rows, before, after)
    for c in summary.checks:
        ops_db.record_dq(
            "silver.apt_trade",
            deal_ym,
            c["name"],
            c["passed"],
            severity="ERROR" if c["blocking"] else "WARN",
            blocking=c["blocking"],
            metric=c["metric"],
            run_id=run_id,
        )
    if all(c["passed"] for c in summary.checks if c["blocking"]):
        ops_db.mark_merged(deal_ym, summary.partitions)
        ops_db.mark_month_merged(deal_ym, summary.silver_snapshot)
    return summary


def post_merge_checks(
    deal_ym: str, parts: list[dict], rows: list[dict], cancel_before: float | None, cancel_after: float | None
) -> list[dict]:
    sgg_list = sgg_in_list(p["sgg_cd"] for p in parts)
    dup = execute(f"""SELECT count(*) FROM (
                        SELECT trade_key, dup_seq FROM lake.silver.apt_trade
                        WHERE is_current AND deal_ym = '{deal_ym}'
                        GROUP BY trade_key, dup_seq HAVING count(*) > 1)""")[0][0]
    # 재조정: 반영 대상 시군구마다 '관측된 현재행 수 = 이번 stage 행 수'
    observed = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.silver.apt_trade
                                WHERE is_current AND missing_since IS NULL AND deal_ym = '{deal_ym}'
                                  AND sgg_cd IN ({sgg_list}) GROUP BY sgg_cd""")
    )
    staged: dict[str, int] = defaultdict(int)
    for r in rows:
        staged[r["sgg_cd"]] += 1
    mismatched = {
        p["sgg_cd"]: (staged.get(p["sgg_cd"], 0), observed.get(p["sgg_cd"], 0))
        for p in parts
        if staged.get(p["sgg_cd"], 0) != observed.get(p["sgg_cd"], 0)
    }
    # 미관측 현재행: (그 파티션의 현재 관측 횟수 - 처음 빠진 관측 순번 + 1) >= 3 이면 경고
    fetch_now = {p["sgg_cd"]: p["fetch_count"] for p in parts}
    missing = execute(f"""SELECT sgg_cd, missing_fetch_seq, count(*) FROM lake.silver.apt_trade
                          WHERE is_current AND missing_since IS NOT NULL AND deal_ym = '{deal_ym}'
                          GROUP BY sgg_cd, missing_fetch_seq""")
    long_missing = sum(
        n for sgg, seq, n in missing if sgg in fetch_now and fetch_now[sgg] - seq + 1 >= MISSING_WARN_OBSERVATIONS
    )
    missing_total = sum(n for _, _, n in missing)
    shift = None if cancel_before is None or cancel_after is None else round(cancel_after - cancel_before, 4)
    return [
        {
            "name": "silver_no_duplicate_current_keys",
            "passed": dup == 0,
            "blocking": True,
            "metric": {"duplicate_keys": dup},
        },
        {
            "name": "silver_matches_stage_per_partition",
            "passed": not mismatched,
            "blocking": True,
            "metric": {"mismatched": mismatched, "partitions": len(parts)},
        },
        {
            "name": "missing_rows_under_3_observations",
            "passed": long_missing == 0,
            "blocking": False,
            "metric": {"missing_current_rows": missing_total, "missing_3plus": long_missing},
        },
        {
            "name": "cancel_ratio_shift_within_10pp",
            "passed": shift is None or abs(shift) <= 0.10,
            "blocking": False,
            "metric": {"before": cancel_before, "after": cancel_after, "shift": shift},
        },
        {
            "name": "dup_fingerprint_groups",
            "passed": True,
            "blocking": False,
            "metric": {"groups": sum(1 for r in rows if r["dup_seq"] == 1), "rows": len(rows)},
        },
    ]


def verify_month(deal_ym: str) -> list[dict]:
    """반영 없이 발행만 이어 갈 때, 현재 레이크 상태로 blocking 검사를 다시 계산한다.
    (마지막 반영의 stage 파티션이 남아 있으므로 '시군구별 stage 행 수 = 관측 현재행 수'를 그대로 확인할 수 있다)"""
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    dup = execute(f"""SELECT count(*) FROM (
                        SELECT trade_key, dup_seq FROM lake.silver.apt_trade
                        WHERE is_current AND deal_ym = '{deal_ym}'
                        GROUP BY trade_key, dup_seq HAVING count(*) > 1)""")[0][0]
    staged = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.stage.apt_trade
                              WHERE deal_ym = '{deal_ym}' GROUP BY sgg_cd""")
    )
    observed = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.silver.apt_trade
                                WHERE is_current AND missing_since IS NULL AND deal_ym = '{deal_ym}'
                                GROUP BY sgg_cd""")
    )
    mismatched = {k: (v, observed.get(k, 0)) for k, v in staged.items() if observed.get(k, 0) != v}
    with ops_db.conn() as c:
        fetch_now = {
            r["sgg_cd"]: r["fetch_count"]
            for r in c.execute(
                "SELECT sgg_cd, fetch_count FROM ops.ingest_partition WHERE deal_ym=%s", (deal_ym,)
            ).fetchall()
        }
    missing = execute(f"""SELECT sgg_cd, missing_fetch_seq, count(*) FROM lake.silver.apt_trade
                          WHERE is_current AND missing_since IS NOT NULL AND deal_ym = '{deal_ym}'
                          GROUP BY sgg_cd, missing_fetch_seq""")
    long_missing = sum(
        n for sgg, seq, n in missing if sgg in fetch_now and fetch_now[sgg] - seq + 1 >= MISSING_WARN_OBSERVATIONS
    )
    groups = execute(f"SELECT count(*) FROM lake.stage.apt_trade WHERE deal_ym = '{deal_ym}' AND dup_seq = 1")[0][0]
    return [
        {
            "name": "silver_no_duplicate_current_keys",
            "passed": dup == 0,
            "blocking": True,
            "metric": {"duplicate_keys": dup, "mode": "publish_only"},
        },
        {
            "name": "silver_matches_stage_per_partition",
            "passed": not mismatched,
            "blocking": True,
            "metric": {"mismatched": mismatched, "partitions": len(staged), "mode": "publish_only"},
        },
        {
            "name": "missing_rows_under_3_observations",
            "passed": long_missing == 0,
            "blocking": False,
            "metric": {
                "missing_current_rows": sum(n for _, _, n in missing),
                "missing_3plus": long_missing,
                "mode": "publish_only",
            },
        },
        {
            # 이번 실행은 silver 를 바꾸지 않으므로 반영 전후 해제 비율이 같다 (변화 0)
            "name": "cancel_ratio_shift_within_10pp",
            "passed": True,
            "blocking": False,
            "metric": {"shift": 0.0, "mode": "publish_only", "reason": "no merge in this run"},
        },
        {
            "name": "dup_fingerprint_groups",
            "passed": True,
            "blocking": False,
            "metric": {"groups": groups, "mode": "publish_only"},
        },
    ]
