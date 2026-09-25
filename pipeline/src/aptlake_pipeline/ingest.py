"""Bronze 수집 (FR-101~104).

한 계약월 파티션 실행 = 그 달의 대상 시군구들을 동시 4개·초당 10회 이하로 호출.
  1) 원본 해시가 마지막 적재본과 같으면 → 변화 없음 (관측 횟수만 증가)
  2) 원본 XML 은 내용 주소(sha256) 키로 raw 버킷에 불변 보관
  3) 파티션별 검사(스키마·필수 필드·건수 급감)에 실패하면 그 시군구만 QUARANTINED → 나머지는 계속
  4) 통과한 행은 bronze.rtms_raw 에 한 번의 커밋으로 append. 같은 원본 해시가 이미 있으면 건너뜀
     (실행이 커밋 직후 죽어 재실행돼도 bronze 중복 0)
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

import boto3
import pyarrow as pa
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from . import ops_db
from .budget import BudgetExhausted
from .config import settings
from .lake import catalog, eq
from .rtms.client import FetchFailed, FetchResult, QuotaExceeded, RtmsClient
from .rtms.parse import REQUIRED_FIELDS, SCHEMA_VERSION, SOURCE_FIELDS_V1, to_typed

log = logging.getLogger(__name__)

BRONZE = "bronze.rtms_raw"
RAW_BUCKET = "raw"
ROW_DROP_RATIO = 0.7  # 이전 적재 대비 30% 넘게 줄면 의심 → 격리
ROW_DROP_MIN_PREV = 20  # 작은 파티션은 자연 변동이 커서 제외

BRONZE_SCHEMA = pa.schema(
    [
        ("ingest_id", pa.string()),
        ("sgg_cd", pa.string()),
        ("deal_ym", pa.string()),
        ("page_no", pa.int32()),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
        ("payload_uri", pa.string()),
        ("payload_sha256", pa.string()),
        ("row_json", pa.string()),
        ("row_ord", pa.int32()),
        ("schema_ver", pa.int32()),
    ]
)


@dataclass
class IngestSummary:
    deal_ym: str
    requested: int = 0
    loaded: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    quarantined: dict[str, str] = field(default_factory=dict)
    retry: dict[str, str] = field(default_factory=dict)
    skipped_budget: list[str] = field(default_factory=list)
    calls: int = 0
    bronze_rows: int = 0
    bronze_snapshot: int | None = None


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    blocking: bool
    metric: dict


def validate(result: FetchResult, rows_last: int | None) -> list[CheckOutcome]:
    """파티션 단위 bronze 검사. blocking 실패가 하나라도 있으면 그 파티션은 적재하지 않는다."""
    items = result.items
    unknown = sorted({k for i in items for k in i} - set(SOURCE_FIELDS_V1))
    missing = sorted({f for i in items for f in REQUIRED_FIELDS if f not in i})
    parse_errors: list[str] = []
    wrong_partition = 0
    for i in items:
        try:
            t = to_typed(i)
            if t.sgg_cd != result.sgg_cd or t.deal_date.strftime("%Y%m") != result.deal_ym:
                wrong_partition += 1
        except (ValueError, KeyError) as e:
            parse_errors.append(str(e)[:120])
    now = len(items)
    drop = rows_last is not None and rows_last >= ROW_DROP_MIN_PREV and now < rows_last * ROW_DROP_RATIO
    change = None if not rows_last else round((now - rows_last) / rows_last, 3)
    return [
        CheckOutcome(
            "bronze_schema_matches_v1",
            not unknown and not missing,
            True,
            {"unknown_fields": unknown, "missing_required": missing},
        ),
        CheckOutcome(
            "rows_parse_and_belong_to_partition",
            not parse_errors and wrong_partition == 0,
            True,
            {"parse_errors": len(parse_errors), "sample": parse_errors[:3], "wrong_partition": wrong_partition},
        ),
        CheckOutcome("row_count_drop_within_30pct", not drop, True, {"prev": rows_last, "now": now}),
        CheckOutcome(
            "row_count_change_within_30pct",
            change is None or abs(change) <= 0.3,
            False,
            {"prev": rows_last, "now": now, "change": change},
        ),
    ]


class RawArchive:
    """raw 버킷 (Object Lock) — ingest 계정은 Put/Get 만 가능."""

    def __init__(self) -> None:
        s = settings()
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            aws_access_key_id=s.s3_ingest_access_key,
            aws_secret_access_key=str(s.s3_ingest_secret_key),
            region_name="us-east-1",
            config=BotoConfig(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def put(self, result: FetchResult) -> str:
        sha = result.sha256
        prefix = f"rtms/deal_ym={result.deal_ym}/sgg_cd={result.sgg_cd}/{sha}"
        for n, body in enumerate(result.pages, start=1):
            key = f"{prefix}/p{n}.xml"
            try:
                self.s3.head_object(Bucket=RAW_BUCKET, Key=key)
                continue  # 내용 주소 키 → 이미 있으면 같은 내용
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                    raise
            self.s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=body, ContentType="application/xml")
        return f"s3://{RAW_BUCKET}/{prefix}/"


def _bronze_rows(result: FetchResult, ingest_id: str, uri: str) -> list[dict]:
    fetched = dt.datetime.fromtimestamp(result.fetched_at, tz=dt.UTC)
    rows, ord_ = [], 0
    for page in result.parsed:
        for item in page.items:
            rows.append(
                {
                    "ingest_id": ingest_id,
                    "sgg_cd": result.sgg_cd,
                    "deal_ym": result.deal_ym,
                    "page_no": page.page_no,
                    "fetched_at": fetched,
                    "payload_uri": uri,
                    "payload_sha256": result.sha256,
                    "row_json": json.dumps(item, ensure_ascii=False, sort_keys=True),
                    "row_ord": ord_,
                    "schema_ver": SCHEMA_VERSION,
                }
            )
            ord_ += 1
    return rows


def ingest_id_for(result: FetchResult) -> str:
    ts = dt.datetime.fromtimestamp(result.fetched_at, tz=dt.UTC).strftime("%Y%m%dT%H%M%S")
    return f"{result.deal_ym}-{result.sgg_cd}-{ts}-{result.sha256[:8]}"


async def _fetch_all(
    client: RtmsClient, deal_ym: str, sgg_codes: list[str], concurrency: int, summary: IngestSummary
) -> list[FetchResult]:
    sem = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    results: list[FetchResult] = []

    async def one(sgg: str) -> None:
        async with sem:
            if stop.is_set():
                summary.skipped_budget.append(sgg)
                return
            ops_db.mark_fetching(deal_ym, sgg)
            try:
                r = await client.fetch(sgg, deal_ym)
                results.append(r)
                summary.calls += r.calls
            except (BudgetExhausted, QuotaExceeded) as e:
                stop.set()
                summary.skipped_budget.append(sgg)
                ops_db.mark_failed(deal_ym, sgg, f"budget: {e}")
                # 예산 소진은 파티션 결함이 아니므로 attempts 를 되돌린다
                with ops_db.conn() as c:
                    c.execute(
                        """UPDATE ops.ingest_partition SET attempts = greatest(attempts - 1, 0),
                                 status='RETRY' WHERE sgg_cd=%s AND deal_ym=%s""",
                        (sgg, deal_ym),
                    )
            except FetchFailed as e:
                status = ops_db.mark_failed(deal_ym, sgg, str(e), quarantine_now=e.code is not None)
                (summary.quarantined if status == "QUARANTINED" else summary.retry)[sgg] = str(e)

    await asyncio.gather(*(one(s) for s in sgg_codes))
    return results


def ingest_month(
    deal_ym: str, sgg_codes: list[str], reserve: Callable[[int], None], run_id: str | None = None
) -> IngestSummary:
    s = settings()
    summary = IngestSummary(deal_ym=deal_ym, requested=len(sgg_codes))
    ops_db.reset_fetching(deal_ym)
    ops_db.ensure_partitions(deal_ym, sgg_codes)
    states = ops_db.partition_states(deal_ym)

    async def run() -> list[FetchResult]:
        client = RtmsClient(str(s.data_go_kr_key), reserve, tps=s.fetch_tps)
        try:
            return await _fetch_all(client, deal_ym, sgg_codes, s.fetch_concurrency, summary)
        finally:
            await client.aclose()

    results = asyncio.run(run())
    archive = RawArchive()
    pending: list[tuple[FetchResult, str, list[dict]]] = []
    for r in sorted(results, key=lambda x: x.sgg_cd):
        st = states[r.sgg_cd]
        fetched_at = dt.datetime.fromtimestamp(r.fetched_at, tz=dt.UTC)
        if st.payload_sha256 == r.sha256:
            ops_db.mark_unchanged(deal_ym, r.sgg_cd, fetched_at, st.status)
            summary.unchanged.append(r.sgg_cd)
            continue
        uri = archive.put(r)  # 격리 대상도 증거로 원본은 보관
        checks = validate(r, st.rows_last)
        for c in checks:
            ops_db.record_dq(
                "bronze.rtms_raw",
                f"{r.sgg_cd}/{deal_ym}",
                c.name,
                c.passed,
                severity="ERROR" if c.blocking else "WARN",
                blocking=c.blocking,
                metric=c.metric,
                run_id=run_id,
            )
        failed = [c.name for c in checks if c.blocking and not c.passed]
        if failed:
            ops_db.mark_failed(deal_ym, r.sgg_cd, f"blocking check failed: {failed}", quarantine_now=True)
            summary.quarantined[r.sgg_cd] = ",".join(failed)
            continue
        ingest_id = ingest_id_for(r)
        pending.append((r, ingest_id, _bronze_rows(r, ingest_id, uri)))

    if pending:
        table = catalog().load_table(BRONZE)
        shas = {r.sha256 for r, _, _ in pending}
        existing = (
            set(
                table.scan(row_filter=eq("deal_ym", deal_ym), selected_fields=("payload_sha256",))
                .to_arrow()
                .column("payload_sha256")
                .to_pylist()
            )
            & shas
        )
        new_rows = [row for r, _, rows in pending if r.sha256 not in existing for row in rows]
        if new_rows:
            table.append(pa.Table.from_pylist(new_rows, schema=BRONZE_SCHEMA))
            summary.bronze_rows = len(new_rows)
        snap = table.refresh().current_snapshot()
        summary.bronze_snapshot = snap.snapshot_id if snap else None
        for r, ingest_id, rows in pending:
            # 이미 bronze 에 같은 원본이 있던 경우(이전 실행이 커밋 후 중단) 그 ingest_id 를 그대로 쓴다
            iid = ingest_id if r.sha256 not in existing else _existing_ingest_id(table, deal_ym, r)
            ops_db.mark_loaded(
                deal_ym,
                r.sgg_cd,
                rows=len(rows),
                sha=r.sha256,
                ingest_id=iid,
                fetched_at=dt.datetime.fromtimestamp(r.fetched_at, tz=dt.UTC),
            )
            summary.loaded.append(r.sgg_cd)
    return summary


def _existing_ingest_id(table, deal_ym: str, r: FetchResult) -> str:

    ids = (
        table.scan(
            row_filter=eq("deal_ym", deal_ym) & eq("payload_sha256", r.sha256),
            selected_fields=("ingest_id",),
            limit=1,
        )
        .to_arrow()
        .column("ingest_id")
        .to_pylist()
    )
    return ids[0] if ids else ingest_id_for(r)
