"""D1 (데이터 제품 설계 계획서 6장): 원본이 A→B→A 로 되돌아와도 SCD2 시각이 뒤집히지 않아야 한다.

같은 원본(해시)이 bronze 에 이미 있으면 수집은 옛 ingest_id 를 다시 쓴다(ingest.py — 중단 후 재실행 대비).
그때 silver 가 그 bronze 행의 fetched_at(옛 A 의 수집 시각)을 새 버전의 valid_from 으로 쓰면,
B 버전은 valid_to(옛 A 시각) < valid_from(B 시각)이 된다. 새 버전의 시각은 '이번에 관측한 시각'
(ops.ingest_partition.last_changed_at — mark_loaded 가 이번 수집 시각으로 채운다)이어야 한다.
"""

from __future__ import annotations

import datetime as dt
import json

import pyarrow as pa

from aptlake_pipeline import silver

T_A = dt.datetime(2026, 10, 1, 1, 0, tzinfo=dt.UTC)  # 처음 A 를 받은 시각 (bronze 에 남은 값)
T_A_AGAIN = dt.datetime(2026, 10, 3, 1, 0, tzinfo=dt.UTC)  # B 를 거쳐 A 가 되돌아온 시각


def _row() -> dict:
    return {
        "sggCd": "11110", "umdNm": "숭인동", "aptNm": "시험단지", "jibun": "1", "excluUseAr": "84.9",
        "dealYear": "2026", "dealMonth": "9", "dealDay": "10", "dealAmount": "50,000", "floor": "5",
        "buildYear": "2000", "cdealType": " ", "cdealDay": " ", "dealingGbn": "중개거래",
        "estateAgentSggNm": "서울 종로구", "rgstDate": " ", "aptDong": " ", "slerGbn": "개인", "buyerGbn": "개인",
        "landLeaseholdGbn": "N",
    }  # fmt: skip


class _Scan:
    def __init__(self, table: pa.Table):
        self.table = table

    def to_arrow(self) -> pa.Table:
        return self.table


class _Bronze:
    def __init__(self, table: pa.Table):
        self.table = table

    def scan(self, **_kw) -> _Scan:
        return _Scan(self.table)


class _Catalog:
    def __init__(self, table: pa.Table):
        self.table = table

    def load_table(self, _name: str) -> _Bronze:
        return _Bronze(self.table)


def test_reused_bronze_payload_takes_this_fetch_time(monkeypatch):
    iid = "202609-11110-20261001T010000-aaaaaaaa"
    bronze = pa.table(
        {
            "ingest_id": [iid],
            "sgg_cd": ["11110"],
            "fetched_at": pa.array([T_A], pa.timestamp("us", tz="UTC")),
            "row_json": [json.dumps(_row(), ensure_ascii=False)],
            "row_ord": [0],
        }
    )
    monkeypatch.setattr(silver, "catalog", lambda: _Catalog(bronze))
    monkeypatch.setattr(silver.ops_db, "umd_aliases", lambda: {})
    parts = [{"sgg_cd": "11110", "last_ingest_id": iid, "fetch_count": 3, "last_changed_at": T_A_AGAIN}]
    [r] = silver._stage_rows("202609", parts)
    assert r["ingested_at"] == T_A_AGAIN


def test_falls_back_to_bronze_time_when_partition_time_missing(monkeypatch):
    iid = "202609-11110-20261001T010000-aaaaaaaa"
    bronze = pa.table(
        {
            "ingest_id": [iid],
            "sgg_cd": ["11110"],
            "fetched_at": pa.array([T_A], pa.timestamp("us", tz="UTC")),
            "row_json": [json.dumps(_row(), ensure_ascii=False)],
            "row_ord": [0],
        }
    )
    monkeypatch.setattr(silver, "catalog", lambda: _Catalog(bronze))
    monkeypatch.setattr(silver.ops_db, "umd_aliases", lambda: {})
    parts = [{"sgg_cd": "11110", "last_ingest_id": iid, "fetch_count": 1, "last_changed_at": None}]
    [r] = silver._stage_rows("202609", parts)
    assert r["ingested_at"] == T_A
