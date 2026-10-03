"""QA-006: 전국 전체 기간 대량 내보내기가 exporter 메모리 상한(400MB)에서 정렬하다 실패했다.

내보내기 질의는 ORDER BY deal_date, trade_id 로 전체를 정렬하는데, 메모리 안에서만 정렬하면 행이 늘수록 상한에 닿는다
(QA 생성 데이터 255만 행: 384 MiB > 381 MiB, MEMORY_LIMIT_EXCEEDED). 운영 거래 표도 같은 규모(269만 행)이고 매달 늘어난다.
운영과 같은 사용자·프로필(users.d)의 전용 ClickHouse 에 운영 규모의 거래를 넣고, 작업자와 같은 경로(Arrow 스트림)로 읽는다.
공용 시험 DB(골든 응답)를 건드리지 않도록 이 모듈만의 컨테이너를 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import clickhouse_connect
import pytest
from testcontainers.community.clickhouse import ClickHouseContainer

from aptlake_api.features.exports import storage

ROOT = Path(__file__).resolve().parents[2]
PW = "test-only-password"
ROWS = 3_000_000


@pytest.fixture(scope="module")
def exporter():
    with (
        ClickHouseContainer("clickhouse/clickhouse-server:26.8", username="admin", password=PW)
        .with_env("CLICKHOUSE_READER_PASSWORD", PW)
        .with_env("CLICKHOUSE_USAGE_PASSWORD", PW)
        .with_env("CLICKHOUSE_PUBLISHER_PASSWORD", PW)
        .with_env("CLICKHOUSE_EXPORT_PASSWORD", PW)
        .with_volume_mapping(
            str(ROOT / "infra/clickhouse/users.d/aptlake-users.xml"), "/etc/clickhouse-server/users.d/aptlake-users.xml"
        )
        .with_volume_mapping(
            str(ROOT / "infra/clickhouse/config.d/aptlake.xml"), "/etc/clickhouse-server/config.d/aptlake.xml"
        ) as c
    ):
        host, port = c.get_container_host_ip(), int(c.get_exposed_port(8123))
        admin = clickhouse_connect.get_client(
            host=host, port=port, username="admin", password=PW, send_receive_timeout=600
        )
        for stmt in (ROOT / "infra/clickhouse/init/01-schema.sql").read_text().split(";"):
            if stmt.strip():
                admin.command(stmt)
        # 운영 규모·모양의 거래 (69개월 × 256 시군구에 고르게, 문자열 열 길이도 비슷하게)
        admin.command(f"""
            INSERT INTO aptlake.trade_current
            WITH cityHash64(number) AS h, addMonths(toDate('2021-01-01'), toUInt32(number % 69)) AS m
            SELECT concat(toString(10000 + h % 256), '-', toString(toYYYYMM(m)), '-', leftPad(lower(hex(h)), 16, '0'), '-0'),
                   toString(10000 + h % 256), m + toIntervalDay(intDiv(h, 7) % 28),
                   concat('c_', leftPad(lower(hex(cityHash64(h % 46080))), 20, '0')), concat('시험아파트', toString(h % 180), '단지'),
                   concat('시험', toString(h % 12), '동'), concat(toString(h % 900), '-', toString(h % 7)),
                   toDecimal64(59.97 + (h % 100), 4), toInt16(1 + h % 30), toUInt32(50000 + h % 150000), 1500.5,
                   0, NULL, m + 40, '', '중개거래', '개인', '개인', toUInt16(1990 + h % 35), 0, 1, now64(3), NULL
            FROM numbers({ROWS}) SETTINGS max_partitions_per_insert_block = 1000""")
        yield clickhouse_connect.get_client(
            host=host, port=port, username="exporter", password=PW, database="aptlake", send_receive_timeout=600
        )


def test_qa_006_full_range_bulk_export_streams_within_exporter_memory_limit(exporter):
    sql, qp = storage.export_query({"from": "2021-01-01", "to": "2026-09-30"})  # pro 플랜: 기간 제한 없음
    rows = 0
    with exporter.query_arrow_stream(sql, parameters=qp) as stream:  # 작업자(write_parquet_and_upload)와 같은 호출
        for batch in stream:
            rows += batch.num_rows
    assert rows == ROWS
