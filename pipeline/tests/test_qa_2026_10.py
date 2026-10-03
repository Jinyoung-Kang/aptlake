"""2026-10 QA: 월 발행이 표 교체 도중 끊겼을 때의 동작 (실제 ClickHouse, 운영과 같은 스키마·서버 설정).

publish_month 는 네 표를 모두 적재·대조한 뒤 표마다 REPLACE PARTITION 을 차례로 한다. 교체 사이에 프로세스가
죽거나 연결이 끊기면 앞의 표만 새 데이터가 된다 (ADR-038 '남는 창'). 발행은 예외로 끝나 needs_publish 가 남고
센서가 발행만 다시 하므로, 다시 발행했을 때 네 표가 같은 버전으로 맞춰지는지를 본다. 그전까지 시험이 없던 경로.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import clickhouse_connect
import pytest
from testcontainers.community.clickhouse import ClickHouseContainer

from aptlake_pipeline import publish

ROOT = Path(__file__).resolve().parents[2]
PW = "test-only-password"
D = dt.date
RM = ["sgg_cd", "month", "reported", "trades", "cancelled", "priced", "outliers",
      "p25_ppm2", "median_ppm2", "p75_ppm2", "low_sample", "dataset_ver"]  # fmt: skip
RU = ["region_id", "level", "month", "reported", "trades", "cancelled", "priced", "outliers",
      "p25_ppm2", "median_ppm2", "p75_ppm2", "low_sample", "dataset_ver"]  # fmt: skip
TC = ["trade_id", "sgg_cd", "deal_date", "complex_key", "apt_nm", "umd_nm", "jibun", "area_m2", "floor",
      "price_manwon", "ppm2", "is_cancelled", "cancel_date", "registered_date", "apt_dong", "deal_kind",
      "seller_type", "buyer_type", "build_year", "is_outlier", "version", "valid_from", "missing_since"]  # fmt: skip
TV = ["trade_id", "deal_date", "version", "valid_from", "valid_to", "is_current", "is_cancelled", "cancel_date",
      "registered_date", "apt_dong", "deal_kind", "seller_type", "buyer_type"]  # fmt: skip
TS = dt.datetime(2024, 3, 1, tzinfo=dt.UTC)


def _statements(path: Path) -> list[str]:
    out = []
    for chunk in path.read_text().split(";"):
        body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


@pytest.fixture(scope="module")
def ch():
    with ClickHouseContainer("clickhouse/clickhouse-server:26.8", username="admin", password=PW).with_volume_mapping(
        str(ROOT / "infra/clickhouse/config.d/aptlake.xml"), "/etc/clickhouse-server/config.d/aptlake.xml"
    ) as c:
        host, port = c.get_container_host_ip(), int(c.get_exposed_port(8123))
        admin = clickhouse_connect.get_client(host=host, port=port, username="admin", password=PW)
        for stmt in _statements(ROOT / "infra/clickhouse/init/01-schema.sql"):
            admin.command(stmt)
        yield clickhouse_connect.get_client(host=host, port=port, username="admin", password=PW, database="aptlake")


def rows(ver: str, price: int) -> dict[str, list[list]]:
    m = D(2024, 2, 1)
    return {
        "region_month": [["11110", m, 2, 2, 0, 2, 0, 900.0, 1000.0, 1100.0, 0, ver]],
        "rollup_month": [["11", "sido", m, 2, 2, 0, 2, 0, 900.0, 1000.0, 1100.0, 0, ver]],
        "trade_current": [
            [
                f"11110-202402-{i:016x}-0",
                "11110",
                D(2024, 2, 10 + i),
                "c_" + "a" * 20,
                "단지",
                "동",
                "1",
                Decimal("84.9"),
                5,
                price,
                price / 84.9,
                0,
                None,
                None,
                "",
                "중개거래",
                "개인",
                "개인",
                2009,
                0,
                1,
                TS,
                None,
            ]
            for i in range(2)
        ],  # fmt: skip
        "trade_version": [
            [
                f"11110-202402-{i:016x}-0",
                D(2024, 2, 10 + i),
                1,
                TS,
                None,
                1,
                0,
                None,
                None,
                "",
                "중개거래",
                "개인",
                "개인",
            ]
            for i in range(2)
        ],  # fmt: skip
    }


COLS = {"region_month": RM, "rollup_month": RU, "trade_current": TC, "trade_version": TV}


def stage_all(data: dict[str, list[list]]):
    def _stage_month(client, deal_ym, dataset_ver, counts):
        for t, rs in data.items():
            counts[t] = publish._stage(client, t, deal_ym, rs, COLS[t], {"n": "count()"}, {"n": len(rs)})

    return _stage_month


def versions(ch) -> dict[str, set]:
    """표마다 2024-02 서빙 행의 버전 표지 (집계 표는 dataset_ver, 거래 표는 가격)."""
    return {
        "region_month": {r[0] for r in ch.query("SELECT dataset_ver FROM region_month").result_rows},
        "rollup_month": {r[0] for r in ch.query("SELECT dataset_ver FROM rollup_month").result_rows},
        "trade_current": {r[0] for r in ch.query("SELECT price_manwon FROM trade_current").result_rows},
        "trade_version": {r[0] for r in ch.query("SELECT count() FROM trade_version").result_rows},
    }


def test_publish_interrupted_between_swaps_recovers_on_retry(ch, monkeypatch):
    for t in COLS:
        ch.command(f"TRUNCATE TABLE {t}")
        ch.command(f"TRUNCATE TABLE {t}_staging")
    monkeypatch.setattr(publish, "ch", lambda: ch)
    monkeypatch.setattr(publish, "_stage_month", stage_all(rows("v1", 100000)))
    publish.publish_month("202402", "v1")
    assert versions(ch)["trade_current"] == {100000} and versions(ch)["region_month"] == {"v1"}

    # v2 발행: 세 번째 표(trade_current) 교체 직전에 연결이 끊긴다
    real_swap = publish._swap

    def swap_or_die(client, table, partition, n_rows):
        if table == "trade_current":
            raise ConnectionError("ClickHouse 연결 끊김 (교체 도중)")
        real_swap(client, table, partition, n_rows)

    monkeypatch.setattr(publish, "_stage_month", stage_all(rows("v2", 200000)))
    monkeypatch.setattr(publish, "_swap", swap_or_die)
    with pytest.raises(ConnectionError):
        publish.publish_month("202402", "v2")
    mixed = versions(ch)
    # 끊긴 순간의 상태: 앞의 두 표만 v2 — 이 창이 있다는 것을 기록 (ADR-038). 발행은 예외로 끝나 needs_publish 가 남는다
    assert mixed["region_month"] == {"v2"} and mixed["rollup_month"] == {"v2"} and mixed["trade_current"] == {100000}

    # 센서가 발행만 다시 하면 (같은 달, 새 버전) 네 표가 모두 맞춰지고 스테이징은 비어 있다
    monkeypatch.setattr(publish, "_swap", real_swap)
    publish.publish_month("202402", "v2")
    after = versions(ch)
    assert after["region_month"] == {"v2"} and after["rollup_month"] == {"v2"} and after["trade_current"] == {200000}
    assert ch.query("SELECT count() FROM trade_current").first_row[0] == 2
    for t in COLS:
        assert ch.query(f"SELECT count() FROM {t}_staging").first_row[0] == 0, t
