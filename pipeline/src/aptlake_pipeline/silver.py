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

매매(silver.apt_trade)와 전월세(silver.apt_rent, ADR-050)는 같은 문장 생성기·검사를 쓰고, 다른 점(표·키 열·
stage 열·보강 열)만 MergeSpec 으로 받는다. 단지 차원·해제 비율·단지 일련번호 검사는 매매에만 있다.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from . import ops_db
from .lake import ENRICH_COLUMNS, catalog, ensure_schema, eq, execute, isin
from .rtms.fingerprint import UmdAliases, assign_keys, display_text
from .rtms.rent_fingerprint import assign_rent_keys
from .sources import RENT, TRADE, SourceSpec

STAGE = "stage.apt_trade"
MISSING_WARN_OBSERVATIONS = 3
_INGEST_COLS = ("ingest_id", "ingested_at", "fetch_seq")

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
        # 상세판 보강 전용 열 (lake.ENRICH_COLUMNS 와 같은 순서) — 지문·속성 해시에 넣지 않는다 (ADR-049)
        *((c, pa.string()) for c in ENRICH_COLUMNS),
    ]
)

# 전월세 stage (lake.DDL 의 stage.apt_rent 와 같은 순서). 금액은 만원, 계약 구분 등은 원천 원문
RENT_STAGE_SCHEMA = pa.schema(
    [
        ("rent_key", pa.string()),
        ("dup_seq", pa.int32()),
        ("rent_id", pa.string()),
        ("attr_hash", pa.string()),
        ("complex_key", pa.string()),
        ("sgg_cd", pa.string()),
        ("deal_ym", pa.string()),
        ("umd_nm", pa.string()),
        ("apt_nm", pa.string()),
        ("jibun", pa.string()),
        ("deal_date", pa.date32()),
        ("deposit_manwon", pa.int64()),
        ("monthly_rent_manwon", pa.int64()),
        ("area_m2", pa.decimal128(9, 4)),
        ("floor", pa.int32()),
        ("build_year", pa.int32()),
        ("contract_type", pa.string()),
        ("contract_term", pa.string()),
        ("use_rr_right", pa.string()),
        ("pre_deposit_manwon", pa.int64()),
        ("pre_monthly_rent_manwon", pa.int64()),
        ("ingest_id", pa.string()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
        ("fetch_seq", pa.int32()),
        ("apt_seq", pa.string()),
        ("road_nm", pa.string()),
    ]
)

STAGE_COLS = [f.name for f in STAGE_SCHEMA]
DATA_COLS = [c for c in STAGE_COLS if c not in _INGEST_COLS]
_SGG = re.compile(r"\d{5}")
_YM = re.compile(r"\d{6}")
_IID = re.compile(r"[0-9A-Za-z\-T]{10,60}")


def _trade_rows(typed: list[Any], aliases: UmdAliases) -> list[dict]:
    out = []
    for k in assign_keys(typed, aliases):
        t = k.trade
        out.append(
            {
                "trade_key": k.trade_key,
                "dup_seq": k.dup_seq,
                "trade_id": k.trade_id,
                "attr_hash": k.attr_hash,
                "complex_key": k.complex_key,
                "sgg_cd": t.sgg_cd,
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
                **{c: getattr(t, c) for c in ENRICH_COLUMNS},
            }
        )
    return out


def _rent_rows(typed: list[Any], aliases: UmdAliases) -> list[dict]:
    out = []
    for k in assign_rent_keys(typed, aliases):
        t = k.rent
        out.append(
            {
                "rent_key": k.rent_key,
                "dup_seq": k.dup_seq,
                "rent_id": k.rent_id,
                "attr_hash": k.attr_hash,
                "complex_key": k.complex_key,
                "sgg_cd": t.sgg_cd,
                "umd_nm": display_text(t.umd_nm),
                "apt_nm": display_text(t.apt_nm),
                "jibun": t.jibun,
                "deal_date": t.deal_date,
                "deposit_manwon": t.deposit_manwon,
                "monthly_rent_manwon": t.monthly_rent_manwon,
                "area_m2": t.area_m2,
                "floor": t.floor,
                "build_year": t.build_year,
                "contract_type": t.contract_type,
                "contract_term": t.contract_term,
                "use_rr_right": t.use_rr_right,
                "pre_deposit_manwon": t.pre_deposit_manwon,
                "pre_monthly_rent_manwon": t.pre_monthly_rent_manwon,
                "apt_seq": t.apt_seq,
                "road_nm": t.road_nm,
            }
        )
    return out


@dataclass(frozen=True)
class MergeSpec:
    source: SourceSpec
    stage: str  # 'stage.apt_trade'
    silver: str  # 'silver.apt_trade'
    key: str  # 지문 열 ('trade_key' | 'rent_key') — (key, dup_seq) 가 현재행 키
    schema: pa.Schema  # stage 열 (표의 열 순서와 같다)
    enrich: tuple[str, ...]  # 보강 전용 열: 새 버전 없이 '속성 같음' 분기에서도 채운다
    keyed_rows: Callable[[list[Any], UmdAliases], list[dict]]
    trade_extras: bool  # 단지 차원·해제 비율·단지 일련번호 검사 (매매만)

    @property
    def stage_cols(self) -> list[str]:
        return [f.name for f in self.schema]

    @property
    def data_cols(self) -> list[str]:
        return [c for c in self.stage_cols if c not in _INGEST_COLS]

    @property
    def asset(self) -> str:
        return self.silver


TRADE_MERGE = MergeSpec(TRADE, STAGE, "silver.apt_trade", "trade_key", STAGE_SCHEMA, ENRICH_COLUMNS, _trade_rows, True)
RENT_MERGE = MergeSpec(RENT, "stage.apt_rent", "silver.apt_rent", "rent_key", RENT_STAGE_SCHEMA,
                       ("apt_seq", "road_nm"), _rent_rows, False)  # fmt: skip


@dataclass
class MergeSummary:
    deal_ym: str
    partitions: list[str] = field(default_factory=list)
    staged_rows: int = 0
    dup_groups: int = 0
    silver_snapshot: int | None = None
    checks: list[dict] = field(default_factory=list)


def _stage_rows(deal_ym: str, parts: list[dict], spec: MergeSpec = TRADE_MERGE) -> list[dict]:
    by_iid = {p["last_ingest_id"]: p for p in parts}
    bronze = catalog().load_table(spec.source.bronze)
    arrow = bronze.scan(
        row_filter=eq("deal_ym", deal_ym) & isin("ingest_id", set(by_iid)),
        selected_fields=("ingest_id", "sgg_cd", "fetched_at", "row_json", "row_ord"),
    ).to_arrow()
    grouped: dict[str, list] = defaultdict(list)
    meta: dict[str, tuple[str, dt.datetime]] = {}
    for rec in arrow.sort_by("row_ord").to_pylist():
        grouped[rec["sgg_cd"]].append(spec.source.to_typed(json.loads(rec["row_json"])))
        meta[rec["sgg_cd"]] = (rec["ingest_id"], rec["fetched_at"])
    rows: list[dict] = []
    aliases = ops_db.umd_aliases()  # 행정구역 승격·개칭: 지문·단지 키는 옛 법정동 이름으로 (표시 이름은 원천 그대로)
    for sgg, typed in grouped.items():
        iid, fetched_at = meta[sgg]
        # 새 버전의 시각은 '이번에 관측한 시각'이다. 같은 원본이 bronze 에 이미 있으면 옛 ingest_id 를 다시 쓰므로
        # (A→B→A) bronze 의 fetched_at 은 옛 시각일 수 있다 → 이번 적재의 수집 시각(mark_loaded 가 채움)을 쓴다 (D1)
        fetched_at = by_iid[iid].get("last_changed_at") or fetched_at
        fetch_seq = by_iid[iid]["fetch_count"]
        for r in spec.keyed_rows(typed, aliases):
            rows.append({**r, "deal_ym": deal_ym, "ingest_id": iid, "ingested_at": fetched_at, "fetch_seq": fetch_seq})
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


def merge_sql(deal_ym: str, parts_values: str, spec: MergeSpec = TRADE_MERGE) -> str:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    key, stage_tbl, silver_tbl = spec.key, spec.stage, spec.silver
    data_cols = spec.data_cols
    cols = ", ".join(data_cols)
    # 세 갈래(UNION ALL)는 위치로 맞춰지므로 stage 열을 이름으로 적어 순서를 고정한다. 'st.*' 는 표의 실제 열 순서를 따라,
    # 열을 더한 표(뒤에 붙음)와 갈래의 순서가 어긋날 수 있었다. MISSING 갈래는 키·수집 열만 있고 나머지는 NULL
    stage_cols = ", ".join(f"st.{c}" for c in spec.stage_cols)
    types = _trino_types(spec.schema)
    from_missing = {key: f"c.{key}", "dup_seq": "c.dup_seq", "ingest_id": "p.ingest_id",
                    "ingested_at": "p.ingested_at", "fetch_seq": "p.fetch_seq"}  # fmt: skip
    missing_cols = ", ".join(from_missing.get(c) or f"CAST(NULL AS {types[c]})" for c in spec.stage_cols)
    insert_cols = (
        f"{cols}, version, valid_from, valid_to, is_current, ingest_id, last_seen_ingest, "
        "last_seen_at, missing_since, missing_fetch_seq"
    )
    # 보강 전용 열은 '속성 같음' 분기에서도 채운다 — 새 버전을 만들지 않고 현재행 그대로 (기본판 행이면 NULL 이라 그대로)
    enrich = "".join(f",\n    {c} = coalesce(src.{c}, t.{c})" for c in spec.enrich)
    insert_vals = (
        ", ".join(f"src.{c}" for c in data_cols)
        + ", src.new_version, src.ingested_at, NULL, true, src.ingest_id, src.ingest_id, "
        "src.ingested_at, NULL, NULL"
    )
    return f"""
MERGE INTO lake.{silver_tbl} t
USING (
    WITH p (sgg_cd, ingest_id, ingested_at, fetch_seq) AS (VALUES {parts_values}),
    s AS (
        SELECT {stage_cols} FROM lake.{stage_tbl} st
        WHERE st.deal_ym = '{deal_ym}' AND st.sgg_cd IN (SELECT sgg_cd FROM p)
    ),
    cur AS (
        SELECT {key}, dup_seq, sgg_cd, attr_hash, version FROM lake.{silver_tbl}
        WHERE is_current AND deal_ym = '{deal_ym}' AND sgg_cd IN (SELECT sgg_cd FROM p)
    )
    SELECT s.{key} AS mk, s.dup_seq AS ms, 'UPSERT' AS action, 1 AS new_version, s.*
    FROM s
    UNION ALL
    SELECT NULL, NULL, 'NEWVER', c.version + 1, s.*
    FROM s JOIN cur c ON c.{key} = s.{key} AND c.dup_seq = s.dup_seq
    WHERE c.attr_hash <> s.attr_hash
    UNION ALL
    SELECT c.{key}, c.dup_seq, 'MISSING', NULL, {missing_cols}
    FROM cur c
    JOIN p ON p.sgg_cd = c.sgg_cd
    LEFT JOIN s ON s.{key} = c.{key} AND s.dup_seq = c.dup_seq
    WHERE s.{key} IS NULL
) src
ON t.{key} = src.mk AND t.dup_seq = src.ms AND t.is_current AND t.deal_ym = '{deal_ym}'
WHEN MATCHED AND src.action = 'MISSING' THEN UPDATE SET
    missing_since = coalesce(t.missing_since, src.ingested_at),
    missing_fetch_seq = coalesce(t.missing_fetch_seq, src.fetch_seq)
WHEN MATCHED AND t.attr_hash <> src.attr_hash THEN UPDATE SET
    is_current = false, valid_to = src.ingested_at
WHEN MATCHED THEN UPDATE SET
    last_seen_ingest = src.ingest_id, last_seen_at = src.ingested_at,
    missing_since = NULL, missing_fetch_seq = NULL{enrich}
WHEN NOT MATCHED AND src.action <> 'MISSING' THEN INSERT ({insert_cols})
    VALUES ({insert_vals})
"""


def _trino_types(schema: pa.Schema = STAGE_SCHEMA) -> dict[str, str]:
    m = {
        pa.string(): "varchar",
        pa.int32(): "integer",
        pa.int64(): "bigint",
        pa.date32(): "date",
        pa.bool_(): "boolean",
        pa.decimal128(9, 4): "decimal(9,4)",
    }
    return {f.name: m[f.type] for f in schema if f.name not in _INGEST_COLS}


# 단지 일련번호(apt_seq): 이번 달 stage 에서 그 단지가 일련번호 하나만 가질 때만 비어 있는 곳을 채운다.
# 여러 개거나 이미 다른 값이면 바꾸지 않고 경고 검사(complex_aptseq_conflicts)에 센다 — 공개 단지 ID 는 complex_key 그대로
COMPLEX_MERGE = """
MERGE INTO lake.silver.apt_complex t
USING (
    SELECT complex_key, min(sgg_cd) AS sgg_cd, min(umd_nm) AS umd_nm, min(jibun) AS jibun, min(apt_nm) AS apt_nm,
           max(build_year) AS build_year, bool_or(land_leasehold) AS land_leasehold, min(deal_date) AS first_seen,
           CASE WHEN count(DISTINCT apt_seq) = 1 THEN min(apt_seq) END AS apt_seq
    FROM lake.stage.apt_trade WHERE deal_ym = '{deal_ym}'
    GROUP BY complex_key
) s
ON t.complex_key = s.complex_key
WHEN MATCHED AND ((t.build_year IS NULL AND s.build_year IS NOT NULL) OR s.first_seen < t.first_seen
                  OR (t.apt_seq IS NULL AND s.apt_seq IS NOT NULL)) THEN UPDATE SET
    build_year = coalesce(t.build_year, s.build_year), first_seen = least(t.first_seen, s.first_seen),
    apt_seq = coalesce(t.apt_seq, s.apt_seq)
WHEN NOT MATCHED THEN INSERT (complex_key, sgg_cd, umd_nm, jibun, apt_nm, build_year, land_leasehold, first_seen, apt_seq)
    VALUES (s.complex_key, s.sgg_cd, s.umd_nm, s.jibun, s.apt_nm, s.build_year, s.land_leasehold, s.first_seen,
            s.apt_seq)
"""


def aptseq_conflicts(deal_ym: str) -> dict[str, int]:
    """이번 달 stage 기준: 한 단지(complex_key)에 일련번호가 여럿이거나 차원 값과 다른 단지 수(conflicts),
    한 일련번호가 단지 키 여럿에 걸친 수(split — 같은 단지의 이름·지번 표기 차이로 단지 키가 갈라진 것)."""
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    conflicts = execute(f"""SELECT count(*) FROM (
                              SELECT s.complex_key FROM lake.stage.apt_trade s
                              LEFT JOIN lake.silver.apt_complex c ON c.complex_key = s.complex_key
                              WHERE s.deal_ym = '{deal_ym}' AND s.apt_seq IS NOT NULL
                              GROUP BY s.complex_key
                              HAVING count(DISTINCT s.apt_seq) > 1 OR max(c.apt_seq) <> min(s.apt_seq))""")[0][0]
    split = execute(f"""SELECT count(*) FROM (
                          SELECT apt_seq FROM lake.stage.apt_trade WHERE deal_ym = '{deal_ym}' AND apt_seq IS NOT NULL
                          GROUP BY apt_seq HAVING count(DISTINCT complex_key) > 1)""")[0][0]
    return {"conflicts": conflicts, "split": split}


def _aptseq_check(metric: dict[str, int], mode: str | None = None) -> dict:
    return {
        "name": "complex_aptseq_conflicts",
        "passed": metric["conflicts"] == 0,
        "blocking": False,
        "metric": {**metric, **({"mode": mode} if mode else {})},
    }


def _cancel_ratio(deal_ym: str) -> float | None:
    rows = execute(f"""SELECT count_if(is_cancelled), count(*) FROM lake.silver.apt_trade
                       WHERE is_current AND deal_ym = '{deal_ym}'""")
    cancelled, total = rows[0] if rows else (0, 0)
    return None if not total else cancelled / total


def merge_month(deal_ym: str, run_id: str | None = None, spec: MergeSpec = TRADE_MERGE) -> MergeSummary:
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    src = spec.source.name
    summary = MergeSummary(deal_ym)
    parts = ops_db.loaded_partitions(deal_ym, source=src)
    if not parts:
        return summary
    rows = _stage_rows(deal_ym, parts, spec)
    summary.partitions = [p["sgg_cd"] for p in parts]
    summary.staged_rows = len(rows)
    summary.dup_groups = sum(1 for r in rows if r["dup_seq"] == 1)

    ingested = {r["sgg_cd"]: r["ingested_at"] for r in rows}
    for p in parts:  # 0건 파티션도 '사라진 거래' 판정에 참여해야 한다
        ingested.setdefault(p["sgg_cd"], dt.datetime.now(tz=dt.UTC))

    ensure_schema()  # 배포 뒤 첫 반영: 새 표·보강 열이 아직 없으면 만든다 (있으면 카탈로그 조회만)
    stage = catalog().load_table(spec.stage)
    arrow = pa.Table.from_pylist(rows, schema=spec.schema)
    stage.overwrite(arrow, overwrite_filter=eq("deal_ym", deal_ym))

    before = _cancel_ratio(deal_ym) if spec.trade_extras else None
    execute(merge_sql(deal_ym, _parts_values(parts, ingested), spec))
    if spec.trade_extras:
        execute(COMPLEX_MERGE.format(deal_ym=deal_ym))
    after = _cancel_ratio(deal_ym) if spec.trade_extras else None
    snap = catalog().load_table(spec.silver).current_snapshot()
    summary.silver_snapshot = snap.snapshot_id if snap else None

    summary.checks = post_merge_checks(deal_ym, parts, rows, before, after, spec)
    for c in summary.checks:
        ops_db.record_dq(
            spec.asset,
            deal_ym,
            c["name"],
            c["passed"],
            severity="ERROR" if c["blocking"] else "WARN",
            blocking=c["blocking"],
            metric=c["metric"],
            run_id=run_id,
        )
    if all(c["passed"] for c in summary.checks if c["blocking"]):
        ops_db.mark_merged(deal_ym, summary.partitions, source=src)
        ops_db.mark_month_merged(deal_ym, summary.silver_snapshot, source=src)
    return summary


def _dup_current_keys(deal_ym: str, spec: MergeSpec) -> int:
    return execute(f"""SELECT count(*) FROM (
                        SELECT {spec.key}, dup_seq FROM lake.{spec.silver}
                        WHERE is_current AND deal_ym = '{deal_ym}'
                        GROUP BY {spec.key}, dup_seq HAVING count(*) > 1)""")[0][0]


def _missing(deal_ym: str, spec: MergeSpec, fetch_now: dict[str, int]) -> tuple[int, int]:
    """(미관측 현재행 수, 그중 3회 이상 연속 미관측). 처음 빠진 관측 순번부터 지금 관측 횟수까지로 센다."""
    missing = execute(f"""SELECT sgg_cd, missing_fetch_seq, count(*) FROM lake.{spec.silver}
                          WHERE is_current AND missing_since IS NOT NULL AND deal_ym = '{deal_ym}'
                          GROUP BY sgg_cd, missing_fetch_seq""")
    long_missing = sum(
        n for sgg, seq, n in missing if sgg in fetch_now and fetch_now[sgg] - seq + 1 >= MISSING_WARN_OBSERVATIONS
    )
    return sum(n for _, _, n in missing), long_missing


def post_merge_checks(
    deal_ym: str,
    parts: list[dict],
    rows: list[dict],
    cancel_before: float | None,
    cancel_after: float | None,
    spec: MergeSpec = TRADE_MERGE,
) -> list[dict]:
    sgg_list = sgg_in_list(p["sgg_cd"] for p in parts)
    dup = _dup_current_keys(deal_ym, spec)
    # 재조정: 반영 대상 시군구마다 '관측된 현재행 수 = 이번 stage 행 수'
    observed = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.{spec.silver}
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
    missing_total, long_missing = _missing(deal_ym, spec, {p["sgg_cd"]: p["fetch_count"] for p in parts})
    checks = [
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
    ]
    if spec.trade_extras:
        shift = None if cancel_before is None or cancel_after is None else round(cancel_after - cancel_before, 4)
        checks.append(
            {
                "name": "cancel_ratio_shift_within_10pp",
                "passed": shift is None or abs(shift) <= 0.10,
                "blocking": False,
                "metric": {"before": cancel_before, "after": cancel_after, "shift": shift},
            }
        )
    checks.append(
        {
            "name": "dup_fingerprint_groups",
            "passed": True,
            "blocking": False,
            "metric": {"groups": sum(1 for r in rows if r["dup_seq"] == 1), "rows": len(rows)},
        }
    )
    if spec.trade_extras:
        checks.append(_aptseq_check(aptseq_conflicts(deal_ym)))
    return checks


def verify_month(deal_ym: str, spec: MergeSpec = TRADE_MERGE) -> list[dict]:
    """반영 없이 발행만 이어 갈 때, 현재 레이크 상태로 blocking 검사를 다시 계산한다.
    (마지막 반영의 stage 파티션이 남아 있으므로 '시군구별 stage 행 수 = 관측 현재행 수'를 그대로 확인할 수 있다)"""
    if not _YM.fullmatch(deal_ym):
        raise ValueError(deal_ym)
    dup = _dup_current_keys(deal_ym, spec)
    staged = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.{spec.stage}
                              WHERE deal_ym = '{deal_ym}' GROUP BY sgg_cd""")
    )
    observed = dict(
        execute(f"""SELECT sgg_cd, count(*) FROM lake.{spec.silver}
                                WHERE is_current AND missing_since IS NULL AND deal_ym = '{deal_ym}'
                                GROUP BY sgg_cd""")
    )
    mismatched = {k: (v, observed.get(k, 0)) for k, v in staged.items() if observed.get(k, 0) != v}
    missing_total, long_missing = _missing(deal_ym, spec, ops_db.fetch_counts(deal_ym, source=spec.source.name))
    groups = execute(f"SELECT count(*) FROM lake.{spec.stage} WHERE deal_ym = '{deal_ym}' AND dup_seq = 1")[0][0]
    checks = [
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
            "metric": {"missing_current_rows": missing_total, "missing_3plus": long_missing, "mode": "publish_only"},
        },
    ]
    if spec.trade_extras:
        checks.append(
            {
                # 이번 실행은 silver 를 바꾸지 않으므로 반영 전후 해제 비율이 같다 (변화 0)
                "name": "cancel_ratio_shift_within_10pp",
                "passed": True,
                "blocking": False,
                "metric": {"shift": 0.0, "mode": "publish_only", "reason": "no merge in this run"},
            }
        )
    checks.append(
        {
            "name": "dup_fingerprint_groups",
            "passed": True,
            "blocking": False,
            "metric": {"groups": groups, "mode": "publish_only"},
        }
    )
    if spec.trade_extras:
        checks.append(_aptseq_check(aptseq_conflicts(deal_ym), "publish_only"))
    return checks
