"""Iceberg 레이크 접근: 테이블 정의(DDL, Trino) + PyIceberg REST 카탈로그 + Trino 연결.

테이블 생성·MERGE·dbt 는 Trino, 대량 append(bronze·stage)는 PyIceberg 로 한다.
두 엔진 모두 같은 Lakekeeper REST 카탈로그를 쓰고, 스토리지 자격증명은 카탈로그가 발급한다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import trino
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.rest import RestCatalog
from pyiceberg.expressions import BooleanExpression, EqualTo, In

from .config import settings

CATALOG = "lake"

DDL: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS lake.bronze",
    "CREATE SCHEMA IF NOT EXISTS lake.stage",
    "CREATE SCHEMA IF NOT EXISTS lake.silver",
    "CREATE SCHEMA IF NOT EXISTS lake.gold",
    # 원본 행 append-only. row_json 은 원천 필드 그대로(문자열)
    """CREATE TABLE IF NOT EXISTS lake.bronze.rtms_raw (
        ingest_id       varchar,
        sgg_cd          varchar,
        deal_ym         varchar,
        page_no         integer,
        fetched_at      timestamp(6) with time zone,
        payload_uri     varchar,
        payload_sha256  varchar,
        row_json        varchar,
        row_ord         integer,
        schema_ver      integer
    ) WITH (format_version = 2, partitioning = ARRAY['deal_ym', 'sgg_cd'])""",
    # MERGE 입력 (월 파티션 단위로 덮어씀). 파싱·지문은 파이썬(단위 테스트 대상)에서 계산
    """CREATE TABLE IF NOT EXISTS lake.stage.apt_trade (
        trade_key varchar, dup_seq integer, trade_id varchar, attr_hash varchar, complex_key varchar,
        sgg_cd varchar, deal_ym varchar, umd_nm varchar, apt_nm varchar, jibun varchar,
        deal_date date, price_manwon bigint, area_m2 decimal(9,4), floor integer, build_year integer,
        is_cancelled boolean, cancel_date date, registered_date date, apt_dong varchar, deal_kind varchar,
        agent_sgg_nm varchar, seller_type varchar, buyer_type varchar, land_leasehold boolean,
        ingest_id varchar, ingested_at timestamp(6) with time zone, fetch_seq integer
    ) WITH (format_version = 2, partitioning = ARRAY['deal_ym'])""",
    # SCD2. PK = (trade_key, dup_seq, valid_from), 현재행 is_current
    """CREATE TABLE IF NOT EXISTS lake.silver.apt_trade (
        trade_key varchar, dup_seq integer, trade_id varchar, version integer,
        valid_from timestamp(6) with time zone, valid_to timestamp(6) with time zone, is_current boolean,
        complex_key varchar, sgg_cd varchar, deal_ym varchar, umd_nm varchar, apt_nm varchar, jibun varchar,
        deal_date date, price_manwon bigint, area_m2 decimal(9,4), floor integer, build_year integer,
        is_cancelled boolean, cancel_date date, registered_date date, apt_dong varchar, deal_kind varchar,
        agent_sgg_nm varchar, seller_type varchar, buyer_type varchar, land_leasehold boolean,
        attr_hash varchar, ingest_id varchar, last_seen_ingest varchar,
        last_seen_at timestamp(6) with time zone,
        missing_since timestamp(6) with time zone, missing_fetch_seq integer
    ) WITH (format_version = 2, partitioning = ARRAY['year(deal_date)'], sorted_by = ARRAY['sgg_cd', 'deal_date'])""",
    """CREATE TABLE IF NOT EXISTS lake.silver.apt_complex (
        complex_key varchar, sgg_cd varchar, umd_nm varchar, jibun varchar, apt_nm varchar,
        build_year integer, land_leasehold boolean, first_seen date
    ) WITH (format_version = 2)""",
    """CREATE TABLE IF NOT EXISTS lake.silver.region (
        sgg_cd varchar, sido_cd varchar, sido_nm varchar, sgg_nm varchar, full_nm varchar,
        source varchar, fetched_at timestamp(6) with time zone
    ) WITH (format_version = 2)""",
    """CREATE TABLE IF NOT EXISTS lake.gold.price_index (
        region_id varchar, method varchar, period varchar, index_value double, ci_low double, ci_high double,
        n_obs integer, model_ver varchar, input_snapshot bigint, computed_at timestamp(6) with time zone
    ) WITH (format_version = 2, partitioning = ARRAY['method'])""",
    """CREATE TABLE IF NOT EXISTS lake.gold.index_reference (
        region_id varchar, period varchar, value double, source varchar, fetched_at timestamp(6) with time zone
    ) WITH (format_version = 2)""",
]


@contextmanager
def trino_conn(user: str = "pipeline") -> Iterator[trino.dbapi.Connection]:
    s = settings()
    conn = trino.dbapi.connect(
        host=s.trino_host, port=s.trino_port, user=user, catalog=CATALOG, http_scheme="http", request_timeout=1800
    )
    try:
        yield conn
    finally:
        conn.close()


def execute(sql: str, params: Sequence[Any] | None = None, user: str = "pipeline") -> list[tuple]:
    """문장을 끝까지 실행하고 결과 행을 돌려준다. 오류는 절대 삼키지 않는다.

    Trino 는 오래 걸리는 문장(MERGE·optimize)의 실패를 결과를 가져오는 도중(fetchall)에 알린다.
    예전 구현은 여기서 TrinoUserError 를 잡아 빈 결과로 돌려줘, 실패한 MERGE(예: 대상 행 하나에 원천 행
    여러 개)가 성공처럼 보일 수 있었다 → 모든 오류를 그대로 올린다.
    """
    with trino_conn(user) as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def ensure_tables() -> None:
    for stmt in DDL:
        execute(stmt)


def catalog() -> Catalog:
    s = settings()
    return RestCatalog(
        "lake",
        uri=s.catalog_uri,
        warehouse=s.catalog_warehouse,
        # Lakekeeper 에 테이블 범위 단기 자격증명 발급을 요청 (엔진은 스토리지 키를 갖지 않음)
        **{"header.X-Iceberg-Access-Delegation": "vended-credentials"},
    )


def current_snapshot_id(table: str) -> int | None:
    snap = catalog().load_table(table).current_snapshot()
    return snap.snapshot_id if snap else None


# PyIceberg 식 클래스는 pydantic 기반이라 mypy 가 위치 인자를 이해하지 못한다 → 타입이 있는 얇은 헬퍼로 모은다
def eq(field: str, value: object) -> BooleanExpression:
    return EqualTo(field, value)  # type: ignore[call-arg,arg-type]


def isin(field: str, values: set | list) -> BooleanExpression:
    return In(field, values)  # type: ignore[call-arg,arg-type]
