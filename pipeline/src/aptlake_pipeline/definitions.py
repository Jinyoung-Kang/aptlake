"""Dagster 정의: 자산·asset check·잡·센서·스케줄.

파티션 설계 (기획서 FR-101 대비 변경):
  Dagster 파티션 = 계약월 (월 1개 = 실행 1개). 시군구 × 월(256 × 수백 개월 = 수만 개)을 Dagster
  파티션으로 두면 실행·이벤트 로그 오버헤드가 수집 비용보다 커지므로, 시군구 단위 상태는
  ops.ingest_partition(상태 머신)에 두고 한 실행 안에서 병렬 수집한다. 시군구 단위 재실행은
  run config(sgg_codes) 또는 관리 API 재시도 → 센서로 한다.

흐름 (month_pipeline): bronze.rtms_raw → silver.apt_trade → gold.* (dbt build) → serving.clickhouse_month
  - bronze·silver 는 blocking asset check 로 하위 전파를 막는다 (FR-301/302)
  - dbt 테스트 실패 시 dbt build 가 실패 → 같은 실행의 ClickHouse 발행이 실행되지 않는다 (FR-303)
"""

import datetime as dt
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import redis
from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetCheckSpec,
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    Config,
    DagsterRunStatus,
    DefaultScheduleStatus,
    DefaultSensorStatus,
    Definitions,
    MaterializeResult,
    MonthlyPartitionsDefinition,
    Output,
    RetryPolicy,
    RunRequest,
    RunsFilter,
    ScheduleDefinition,
    SensorEvaluationContext,
    SkipReason,
    asset,
    define_asset_job,
    sensor,
)
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

from . import ingest, lake, maintenance, ops_db, publish, regions, rone, silver
from .budget import PRIORITIES, Budget, ceilings, kst_today
from .config import settings
from .lake import eq, isin
from .scheduling import Planned, plan_runs, run_calls

log = logging.getLogger(__name__)

BACKFILL_FROM = os.environ.get("BACKFILL_FROM", "2021-01").replace("-", "")
monthly = MonthlyPartitionsDefinition(
    start_date=BACKFILL_FROM,
    fmt="%Y%m",
    timezone="Asia/Seoul",
    end_offset=1,
)
_YM = re.compile(r"\d{6}")
PRIORITY_TAG = {"incremental": "10", "retry": "8", "recheck": "5", "backfill": "0"}
# Trino·ClickHouse 를 쓰는 단계는 한 번 자동 재시도한다. 도커 VM 메모리가 모자라 커널이 가장 큰 프로세스(Trino)를
# 죽인 일이 있었다 — 재시작(약 1분) 뒤 다시 하면 되는 실패였는데 실행 전체가 실패로 끝나 다음 날 스케줄까지 비었다.
# 모두 멱등(같은 입력으로 다시 해도 결과 동일)이라 재시도가 안전하다. 원천 API 를 부르는 단계는 호출 예산을 쓰므로 제외
TRANSIENT_RETRY = RetryPolicy(max_retries=1, delay=90)

DBT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", Path(__file__).resolve().parents[2] / "dbt"))
dbt_project = DbtProject(project_dir=DBT_DIR, profiles_dir=DBT_DIR)


def _budget() -> Budget:
    s = settings()
    return Budget(redis.Redis.from_url(str(s.redis_url)), str(s.pg_dsn), "rtms", s.rtms_daily_limit, s.rtms_daily_cap)


def _recheck_pending() -> bool:
    with ops_db.conn() as c:
        return bool(
            c.execute(
                """SELECT 1 FROM ops.ingest_partition
               WHERE next_due_at <= now() AND status IN ('MERGED','RETRY')
                 AND to_date(deal_ym, 'YYYYMM') BETWEEN (date_trunc('month', now()) - interval '12 months')
                                                    AND (date_trunc('month', now()) - interval '3 months')
               LIMIT 1"""
            ).fetchone()
        )


# ───────────────────────── 차원: 시군구 ─────────────────────────


@asset(
    key=AssetKey(["silver", "region"]),
    group_name="silver",
    pool="trino_write",
    description="행정안전부 법정동코드 API → 수집 대상 시군구(leaf) 목록",
)
def region_asset(context: AssetExecutionContext) -> MaterializeResult:
    lake.ensure_tables()
    stats = regions.refresh()
    return MaterializeResult(metadata=stats)


@asset(
    key=AssetKey(["silver", "region_boundary"]),
    group_name="silver",
    deps=[AssetKey(["silver", "region"])],
    check_specs=[AssetCheckSpec("boundary_matches_official_regions", asset=AssetKey(["silver", "region_boundary"]))],
    description="V-World 시군구 경계 → 공식 시군구 목록과 코드·이름 대조 → 위상 보존 단순화 (지도용)",
)
def region_boundary(context: AssetExecutionContext):
    from . import boundary

    r = boundary.refresh()
    ok = not (r.unmatched_source or r.missing_official or r.name_mismatch)
    metric = {
        "matched": r.matched,
        "unmatched_source": r.unmatched_source,
        "missing_official": r.missing_official,
        "name_mismatch": {k: list(v) for k, v in r.name_mismatch.items()},
    }
    ops_db.record_dq(
        "silver.region_boundary",
        None,
        "boundary_matches_official_regions",
        ok,
        severity="WARN",
        metric=metric,
        run_id=context.run_id,
    )
    yield AssetCheckResult(
        check_name="boundary_matches_official_regions",
        passed=ok,
        metadata={
            "matched": r.matched,
            "problems": json.dumps({k: v for k, v in metric.items() if k != "matched"}, ensure_ascii=False)[:2000],
        },
        severity=AssetCheckSeverity.WARN,
    )
    yield Output(
        None,
        metadata={
            "matched": r.matched,
            "raw_bytes": r.raw_bytes,
            "raw_sha256": r.raw_sha256,
            "simplified_bytes": r.out_bytes,
            "area_rel_error_median": round(r.area_err_median, 5),
            "area_rel_error_max": round(r.area_err_max, 5),
            "neighbor_overlap_pct": r.overlap_pct,
            "neighbor_gap_pct": r.gap_pct,
        },
    )


# ───────────────────────── Bronze ─────────────────────────


class BronzeConfig(Config):
    priority: str = "backfill"
    sgg_codes: list[str] = []  # 비우면 수집 대상 전체
    # False = 원천 호출 없이 하위(silver 반영·gold·발행)만 이어서 실행 — 발행 전에 죽은 달을 복구할 때
    fetch: bool = True


BRONZE_CHECKS = [
    AssetCheckSpec(
        "loaded_rows_match_schema_v1",
        asset=AssetKey(["bronze", "rtms_raw"]),
        blocking=True,
        description="silver 로 넘어갈 LOADED 파티션의 bronze 행이 원천 스키마 v1 과 일치하고 모두 파싱된다",
    ),
    AssetCheckSpec(
        "quarantine_rate_below_20pct",
        asset=AssetKey(["bronze", "rtms_raw"]),
        blocking=True,
        description="한 달 수집에서 격리 비율이 20% 이상이면 원천 측 체계적 문제로 보고 하위 반영을 멈춘다",
    ),
]


@asset(
    key=AssetKey(["bronze", "rtms_raw"]),
    group_name="bronze",
    partitions_def=monthly,
    pool="rtms",
    deps=[AssetKey(["silver", "region"])],
    check_specs=BRONZE_CHECKS,
    output_required=False,
    description="실거래 원본: s3://raw 에 원본 XML 불변 보관 + bronze.rtms_raw append (FR-101~104)",
)
def bronze_rtms(context: AssetExecutionContext, config: BronzeConfig):
    ym = context.partition_key
    if config.priority not in (*PRIORITIES, "retry"):
        raise ValueError(f"unknown priority {config.priority}")
    targets = config.sgg_codes or ops_db.leaf_regions()
    if not targets:
        raise RuntimeError("no regions — materialize silver/region first")
    budget = _budget()
    ceil = ceilings(settings().rtms_daily_cap, len(ops_db.leaf_regions()), _recheck_pending())
    prio = "recheck" if config.priority == "retry" else config.priority
    limit = ceil.for_priority(prio)

    def reserve(n: int) -> None:
        budget.reserve(n, prio, limit)

    if config.fetch:
        try:
            summary = ingest.ingest_month(ym, targets, reserve, run_id=context.run_id)
            if summary.source_stopped:
                # 한도 초과·인증키 오류 모두 그날 수집을 멈춘다 (센서가 자정까지 실행을 만들지 않음, 사유는 수집 상태 화면에)
                context.log.warning(f"source stopped for today: {summary.source_stopped}")
                budget.mark_exhausted(summary.source_stopped)
        finally:
            budget.persist()
    else:
        summary = ingest.IngestSummary(deal_ym=ym)
    context.log.info(
        f"{ym}: loaded={len(summary.loaded)} unchanged={len(summary.unchanged)} "
        f"quarantined={len(summary.quarantined)} retry={len(summary.retry)} "
        f"budget_skipped={len(summary.skipped_budget)} calls={summary.calls}"
    )

    loaded = ops_db.loaded_partitions(ym)
    fetched = len(summary.loaded) + len(summary.unchanged) + len(summary.quarantined)
    q_rate = (len(summary.quarantined) / fetched) if fetched else 0.0
    schema_ok, bad = _verify_loaded_bronze(ym, loaded)
    for name, passed, metric in (
        ("loaded_rows_match_schema_v1", schema_ok, {"bad_rows": bad}),
        ("quarantine_rate_below_20pct", q_rate < 0.2, {"quarantined": len(summary.quarantined), "fetched": fetched}),
    ):
        ops_db.record_dq("bronze.rtms_raw", ym, name, passed, blocking=True, metric=metric, run_id=context.run_id)
        yield AssetCheckResult(check_name=name, passed=passed, metadata=metric, severity=AssetCheckSeverity.ERROR)
    publish_pending = ops_db.month_needs_publish(ym)
    # 반영할 변경이 있거나 발행이 밀린 달일 때만 물질화 → 둘 다 없으면 같은 실행의 하위 자산은 건너뜀
    if loaded or publish_pending:
        yield Output(
            None,
            metadata={
                "loaded": len(summary.loaded),
                "unchanged": len(summary.unchanged),
                "quarantined": json.dumps(summary.quarantined, ensure_ascii=False)[:1000],
                "retry": len(summary.retry),
                "budget_skipped": len(summary.skipped_budget),
                "api_calls": summary.calls,
                "bronze_rows_appended": summary.bronze_rows,
                "bronze_snapshot": str(summary.bronze_snapshot),
                "pending_merge_partitions": len(loaded),
                "publish_pending": publish_pending,
            },
        )


def _verify_loaded_bronze(ym: str, loaded: list[dict]) -> tuple[bool, int]:

    from .rtms.parse import SOURCE_FIELDS_V1, to_typed

    iids = {p["last_ingest_id"] for p in loaded if p["rows_last"]}
    if not iids:
        return True, 0
    tbl = lake.catalog().load_table("bronze.rtms_raw")
    bad = 0
    arrow = tbl.scan(
        row_filter=eq("deal_ym", ym) & isin("ingest_id", iids), selected_fields=("row_json", "schema_ver")
    ).to_arrow()
    for rec in arrow.to_pylist():
        row = json.loads(rec["row_json"])
        try:
            if set(row) - set(SOURCE_FIELDS_V1) or rec["schema_ver"] != 1:
                raise ValueError("schema")
            to_typed(row)
        except (ValueError, KeyError):
            bad += 1
    return bad == 0, bad


# ───────────────────────── Silver ─────────────────────────

SILVER_KEY = AssetKey(["silver", "apt_trade"])
SILVER_CHECKS = [
    AssetCheckSpec("silver_no_duplicate_current_keys", asset=SILVER_KEY, blocking=True),
    AssetCheckSpec("silver_matches_stage_per_partition", asset=SILVER_KEY, blocking=True),
    AssetCheckSpec("missing_rows_under_3_observations", asset=SILVER_KEY),
    AssetCheckSpec("cancel_ratio_shift_within_10pp", asset=SILVER_KEY),
    AssetCheckSpec("dup_fingerprint_groups", asset=SILVER_KEY),
]


@asset(
    key=SILVER_KEY,
    group_name="silver",
    partitions_def=monthly,
    pool="trino_write",
    deps=[AssetKey(["bronze", "rtms_raw"])],
    check_specs=SILVER_CHECKS,
    retry_policy=TRANSIENT_RETRY,
    output_required=False,
    description="지문 키 + dup_seq, 한 문장 MERGE 로 SCD2 반영, 사라진 거래 missing_since (FR-201~205)",
)
def silver_apt_trade(context: AssetExecutionContext):
    ym = context.partition_key
    summary = silver.merge_month(ym, run_id=context.run_id)
    if not summary.partitions:
        if ops_db.month_needs_publish(ym):
            # silver 는 이미 반영돼 있고 발행만 밀린 달 → 현재 상태로 blocking 검사를 다시 한 뒤 하위로 넘긴다
            context.log.info(f"{ym}: nothing to merge, publish pending")
            for c in silver.verify_month(ym):
                blocking = c["blocking"]
                ops_db.record_dq(
                    "silver.apt_trade",
                    ym,
                    c["name"],
                    c["passed"],
                    severity="ERROR" if blocking else "WARN",
                    blocking=blocking,
                    metric=c["metric"],
                    run_id=context.run_id,
                )
                yield AssetCheckResult(
                    check_name=c["name"],
                    passed=c["passed"],
                    metadata=c["metric"],
                    severity=AssetCheckSeverity.ERROR if blocking else AssetCheckSeverity.WARN,
                )
            yield Output(None, metadata={"partitions": 0, "publish_pending": True})
        else:
            context.log.info(f"{ym}: nothing to merge")
        return
    for c in summary.checks:
        yield AssetCheckResult(
            check_name=c["name"],
            passed=c["passed"],
            metadata=c["metric"],
            severity=AssetCheckSeverity.ERROR if c["blocking"] else AssetCheckSeverity.WARN,
        )
    yield Output(
        None,
        metadata={
            "partitions": len(summary.partitions),
            "staged_rows": summary.staged_rows,
            "silver_snapshot": str(summary.silver_snapshot),
        },
    )


# ───────────────────────── Gold (dbt) ─────────────────────────


class Translator(DagsterDbtTranslator):
    def get_asset_spec(self, manifest, unique_id, project):
        spec = super().get_asset_spec(manifest, unique_id, project)
        props = self.get_resource_props(manifest, unique_id)
        if props["resource_type"] == "model":
            return spec.replace_attributes(key=AssetKey(["gold", props["name"]]), group_name="gold")
        return spec


@dbt_assets(
    manifest=dbt_project.manifest_path,
    select="trade_serving region_month region_rollup_month trade_version",
    partitions_def=monthly,
    dagster_dbt_translator=Translator(),
    name="gold_monthly",
    pool="trino_write",
    retry_policy=TRANSIENT_RETRY,
)
def gold_monthly(context: AssetExecutionContext, dbt: DbtCliResource):
    ym = context.partition_key
    if not _YM.fullmatch(ym):
        raise ValueError(ym)
    yield from dbt.cli(["build", "--vars", json.dumps({"deal_ym": ym})], context=context).stream()


@dbt_assets(
    manifest=dbt_project.manifest_path,
    select="complex_summary",
    dagster_dbt_translator=Translator(),
    name="gold_dimensions",
    pool="trino_write",
    retry_policy=TRANSIENT_RETRY,
)
def gold_dimensions(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["build"], context=context).stream()


# ───────────────────────── 서빙 발행 ─────────────────────────


@asset(
    key=AssetKey(["serving", "clickhouse_month"]),
    group_name="serving",
    partitions_def=monthly,
    pool="publish",
    retry_policy=TRANSIENT_RETRY,
    deps=[
        AssetKey(["gold", "region_month"]),
        AssetKey(["gold", "region_rollup_month"]),
        AssetKey(["gold", "trade_serving"]),
        AssetKey(["gold", "trade_version"]),
    ],
    description="gold 월 파티션 → ClickHouse 스테이징 → 대조 → REPLACE PARTITION, 데이터셋 버전 증가",
)
def clickhouse_month(context: AssetExecutionContext) -> MaterializeResult:
    ym = context.partition_key
    version = publish.next_dataset_version()
    counts = publish.publish_month(ym, version)
    publish.commit_version(version, [ym], counts, context.run_id)
    ops_db.mark_month_published(ym, version)
    return MaterializeResult(metadata={"dataset_version": version, **counts})


@asset(
    key=AssetKey(["serving", "clickhouse_dimensions"]),
    group_name="serving",
    pool="publish",
    retry_policy=TRANSIENT_RETRY,
    deps=[AssetKey(["gold", "complex_summary"]), AssetKey(["silver", "region"])],
)
def clickhouse_dimensions(context: AssetExecutionContext) -> MaterializeResult:
    counts = publish.publish_dimensions()
    # 서빙 내용이 바뀌었으니 버전을 올린다 — API 결과 캐시·ETag 가 버전을 키로 써서, 올리지 않으면
    # 다음 월 발행 전까지 이전 단지·지역 응답(304 포함)이 계속 나갔다
    version = publish.next_dataset_version()
    publish.commit_version(version, [], counts, context.run_id)
    return MaterializeResult(metadata={"dataset_version": version, **counts})


# ───────────────────────── 지수 ─────────────────────────


@asset(
    key=AssetKey(["gold", "index_reference"]),
    group_name="index",
    deps=[AssetKey(["silver", "region"])],
    description="R-ONE A_2024_00178 (월) 지역별 매매지수_아파트 — 검증 기준값",
)
def index_reference(context: AssetExecutionContext) -> MaterializeResult:
    return MaterializeResult(metadata={k: str(v) for k, v in rone.refresh().items()})


@asset(
    key=AssetKey(["gold", "price_index"]),
    group_name="index",
    pool="publish",
    retry_policy=TRANSIENT_RETRY,
    deps=[AssetKey(["gold", "trade_serving"]), AssetKey(["gold", "index_reference"])],
    description="HEDONIC_TD_v1 (전국·시도) + R-ONE 대비 월간 변화율 상관·방향 일치율 → Iceberg + ClickHouse",
)
def price_index(context: AssetExecutionContext) -> MaterializeResult:
    from .index_job import build_index

    result = build_index(context.log)
    if result.get("points"):  # 발행했을 때만 (완결된 달이 없으면 발행 없이 끝남) — 버전을 올리는 이유는 위와 같음
        version = publish.next_dataset_version()
        publish.commit_version(version, [], {"price_index": int(str(result["points"]))}, context.run_id)
        result["dataset_version"] = version
    return MaterializeResult(metadata={k: v if isinstance(v, int | float) else str(v) for k, v in result.items()})


# ───────────────────────── 유지보수 (FR-601) ─────────────────────────


@asset(
    key=AssetKey(["ops", "iceberg_maintenance"]),
    group_name="ops",
    pool="trino_write",
    description="작은 파일 압축(optimize), 스냅샷 만료, 고아 파일 정리 — 주 1회",
)
def iceberg_maintenance(context: AssetExecutionContext) -> MaterializeResult:
    return MaterializeResult(metadata={k: json.dumps(v) for k, v in maintenance.run().items()})


# ───────────────────────── 잡·센서·스케줄 ─────────────────────────

month_pipeline = define_asset_job(
    "month_pipeline",
    selection=AssetSelection.keys(
        AssetKey(["bronze", "rtms_raw"]), SILVER_KEY, AssetKey(["serving", "clickhouse_month"])
    )
    | AssetSelection.assets(gold_monthly),
    partitions_def=monthly,
)
dims_and_index = define_asset_job(
    "dims_and_index",
    selection=AssetSelection.assets(gold_dimensions)
    | AssetSelection.keys(
        AssetKey(["serving", "clickhouse_dimensions"]),
        AssetKey(["gold", "index_reference"]),
        AssetKey(["gold", "price_index"]),
    ),
)
regions_job = define_asset_job(
    "refresh_regions",
    selection=AssetSelection.keys(AssetKey(["silver", "region"]), AssetKey(["silver", "region_boundary"])),
)
maintenance_job = define_asset_job(
    "iceberg_maintenance", selection=AssetSelection.keys(AssetKey(["ops", "iceberg_maintenance"]))
)


def _run_request(p: Planned, now: dt.datetime) -> RunRequest:
    if not p.fetch:  # 발행 전용 — 원천 호출 없이 하위 단계만
        return RunRequest(
            run_key=f"{p.ym}-publish-{now:%Y%m%d%H%M}",
            partition_key=p.ym,
            run_config={"ops": {"bronze__rtms_raw": {"config": {"priority": "retry", "fetch": False}}}},
            tags={"dagster/priority": PRIORITY_TAG["retry"], "aptlake/priority": "publish-only"},
        )
    return RunRequest(
        run_key=f"{p.ym}-{now:%Y%m%d%H%M}",
        partition_key=p.ym,
        run_config={"ops": {"bronze__rtms_raw": {"config": {"priority": p.priority, "sgg_codes": list(p.sgg_codes)}}}},
        tags={"dagster/priority": PRIORITY_TAG[p.priority], "aptlake/priority": p.priority},
    )


@sensor(job=month_pipeline, minimum_interval_seconds=300, default_status=DefaultSensorStatus.STOPPED)
def due_partitions_sensor(context: SensorEvaluationContext):
    """수집 기한이 된 (시군구, 월)을 월별로 묶어 실행 요청. 증분 > 재시도 > 재확인 > 백필 순, 예산 안에서만."""
    leaf = ops_db.leaf_regions()
    if not leaf:
        return SkipReason("no regions yet")
    today = kst_today()
    keys = monthly.get_partition_keys()
    # 새 달·백필 대상 파티션 등록 (PENDING, 즉시 기한). 격자 모양(월 범위·시군구 목록)이 바뀔 때만 —
    # 5분마다 약 1.8만 행 INSERT … ON CONFLICT 를 되풀이하지 않도록 모양의 지문을 센서 커서에 둔다
    shape = hashlib.sha256(f"{keys[0]}:{keys[-1]}:{','.join(leaf)}".encode()).hexdigest()[:16]
    if context.cursor != shape:
        ops_db.ensure_grid(list(keys), leaf)
        context.update_cursor(shape)
    with ops_db.conn() as c:
        # 파티션 정의 범위 안의 달, 현존 수집 대상 시군구만 — 범위 밖 행(시험용 합성 파티션, 시작 월 변경,
        # 통합·폐지된 시군구)이 하나라도 섞이면 알 수 없는 파티션 키로 틱 전체가 실패했다
        due = c.execute(
            """SELECT deal_ym, status, array_agg(sgg_cd ORDER BY sgg_cd) AS sgg
               FROM ops.ingest_partition
               WHERE ((next_due_at <= now() AND status IN ('PENDING','RETRY','MERGED','LOADED'))
                      -- 죽은 실행이 남긴 FETCHING (1시간 넘게 갱신 없음) 도 다시 수집
                      OR (status = 'FETCHING' AND updated_at < now() - interval '1 hour'))
                 AND deal_ym BETWEEN %s AND %s AND sgg_cd = ANY(%s)
               GROUP BY deal_ym, status""",
            (keys[0], keys[-1], list(leaf)),
        ).fetchall()
    active = context.instance.get_runs(
        filters=RunsFilter(
            job_name="month_pipeline",
            statuses=[DagsterRunStatus.QUEUED, DagsterRunStatus.STARTING, DagsterRunStatus.STARTED],
        )
    )
    in_flight = {r.tags.get("dagster/partition") for r in active}
    # 아직 끝나지 않은 요청의 예상 호출 수도 예산에서 뺀다 (틱마다 같은 예산을 다시 쓰지 않도록)
    queued_calls = sum(run_calls(r.run_config, len(leaf)) for r in active)
    used = _budget().snapshot().get("used", 0)
    planned = plan_runs(
        due,
        today=today,
        in_flight=in_flight,
        used=used,
        queued_calls=queued_calls,
        ceilings=ceilings(settings().rtms_daily_cap, len(leaf), _recheck_pending()),
        needing_publish=ops_db.months_needing_publish(),
        partition_keys=keys,
    )
    now = dt.datetime.now(tz=dt.UTC)
    requests = [_run_request(p, now) for p in planned]
    if not requests:
        return SkipReason(f"nothing due within budget (used {used}, queued {queued_calls})")
    return requests


definitions_schedules = [
    ScheduleDefinition(
        job=dims_and_index,
        cron_schedule="30 5 * * *",
        execution_timezone="Asia/Seoul",
        default_status=DefaultScheduleStatus.STOPPED,
        name="daily_dims_and_index",
    ),
    ScheduleDefinition(
        job=regions_job,
        cron_schedule="0 2 1 * *",
        execution_timezone="Asia/Seoul",
        default_status=DefaultScheduleStatus.STOPPED,
        name="monthly_regions",
    ),
    ScheduleDefinition(
        job=maintenance_job,
        cron_schedule="0 4 * * 0",
        execution_timezone="Asia/Seoul",
        default_status=DefaultScheduleStatus.STOPPED,
        name="weekly_iceberg_maintenance",
    ),
]

silver_complex_spec = AssetSpec(
    key=AssetKey(["silver", "apt_complex"]),
    deps=[SILVER_KEY],
    group_name="silver",
    description="단지 차원 (complex_key). silver.apt_trade 자산이 같은 실행에서 MERGE 로 함께 갱신한다 (FR-205)",
)

defs = Definitions(
    assets=[
        silver_complex_spec,
        region_boundary,
        region_asset,
        bronze_rtms,
        silver_apt_trade,
        gold_monthly,
        gold_dimensions,
        clickhouse_month,
        clickhouse_dimensions,
        index_reference,
        price_index,
        iceberg_maintenance,
    ],
    jobs=[month_pipeline, dims_and_index, regions_job, maintenance_job],
    sensors=[due_partitions_sensor],
    schedules=definitions_schedules,
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
