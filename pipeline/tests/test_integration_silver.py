"""정합성 통합 테스트 (기획서 12장): 실제 Lakekeeper + MinIO + Trino 에서 MERGE·SCD2 를 검증한다.

실행: make test-integration   (dagster 컨테이너 안에서 pytest -m integration)
합성 파티션(sgg 99999, 계약월 209901)만 쓰고, 끝나면 모두 지운다.
"""

from __future__ import annotations

import datetime as dt
import json

import pyarrow as pa
import pytest

pytestmark = pytest.mark.integration

SGG, YM = "99999", "209901"


def row(apt="테스트단지", floor="5", amount="50,000", day="10", cancel=" ", cancel_day=" ", rgst=" "):
    return {
        "sggCd": SGG,
        "umdNm": "테스트동",
        "aptNm": apt,
        "jibun": "1-1",
        "excluUseAr": "84.9",
        "dealYear": "2099",
        "dealMonth": "1",
        "dealDay": day,
        "dealAmount": amount,
        "floor": floor,
        "buildYear": "2000",
        "cdealType": cancel,
        "cdealDay": cancel_day,
        "dealingGbn": "중개거래",
        "estateAgentSggNm": "테스트",
        "rgstDate": rgst,
        "aptDong": " ",
        "slerGbn": "개인",
        "buyerGbn": "개인",
        "landLeaseholdGbn": "N",
    }


A = row()
A_DUP = row()  # A 와 지문이 같은 거래 (dup_seq 0/1)
B = row(apt="다른단지", amount="30,000")
D = row(apt="새단지", amount="70,000")
A_CANCELLED = row(cancel="O", cancel_day="99.01.20")


@pytest.fixture(scope="module")
def env():
    from aptlake_pipeline import lake, ops_db

    lake.ensure_tables()
    _cleanup()
    with ops_db.conn() as c:
        c.execute("INSERT INTO ops.ingest_partition (sgg_cd, deal_ym) VALUES (%s, %s)", (SGG, YM))
    yield
    _cleanup()


def _cleanup():
    from aptlake_pipeline import ops_db
    from aptlake_pipeline.lake import execute

    # 실제 운영 DB·레이크를 같이 쓰므로 반영 단계가 남기는 흔적(단지 차원·발행 대기 표시)까지 모두 지운다
    for t in ("bronze.rtms_raw", "stage.apt_trade", "silver.apt_trade", "silver.apt_complex"):
        execute(f"DELETE FROM lake.{t} WHERE sgg_cd = '{SGG}'")
    with ops_db.conn() as c:
        c.execute("DELETE FROM ops.ingest_partition WHERE sgg_cd = %s", (SGG,))
        c.execute("DELETE FROM ops.dq_result WHERE partition = %s", (YM,))
        c.execute("DELETE FROM ops.month_state WHERE deal_ym = %s", (YM,))


_seq = {"n": 0}


def load(items: list[dict]) -> None:
    """수집 1회를 흉내: bronze 에 새 ingest 로 append, ops 상태 LOADED."""
    from aptlake_pipeline import ops_db
    from aptlake_pipeline.ingest import BRONZE_SCHEMA
    from aptlake_pipeline.lake import catalog

    _seq["n"] += 1
    iid = f"{YM}-{SGG}-20990201T00000{_seq['n']}-test"
    now = dt.datetime.now(tz=dt.UTC)
    rows = [
        {
            "ingest_id": iid,
            "sgg_cd": SGG,
            "deal_ym": YM,
            "page_no": 1,
            "fetched_at": now,
            "payload_uri": "s3://raw/test/",
            "payload_sha256": f"{_seq['n']:064d}",
            "row_json": json.dumps(i, ensure_ascii=False, sort_keys=True),
            "row_ord": n,
            "schema_ver": 1,
        }
        for n, i in enumerate(items)
    ]
    catalog().load_table("bronze.rtms_raw").append(pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA))
    with ops_db.conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='LOADED', last_ingest_id=%s, fetch_count=fetch_count+1,
                     rows_last=%s WHERE sgg_cd=%s AND deal_ym=%s""",
            (iid, len(items), SGG, YM),
        )


def merge():
    from aptlake_pipeline import silver

    s = silver.merge_month(YM, run_id="itest")
    assert all(c["passed"] for c in s.checks if c["blocking"]), s.checks
    return s


def state():
    from aptlake_pipeline.lake import execute

    return execute(f"""SELECT apt_nm, dup_seq, version, is_current, is_cancelled, valid_to IS NOT NULL,
                              missing_since IS NOT NULL
                       FROM lake.silver.apt_trade WHERE sgg_cd = '{SGG}'
                       ORDER BY apt_nm, dup_seq, version""")


def force_reload():
    from aptlake_pipeline import ops_db

    with ops_db.conn() as c:
        c.execute("UPDATE ops.ingest_partition SET status='LOADED' WHERE sgg_cd=%s AND deal_ym=%s", (SGG, YM))


def test_scd2_lifecycle(env):
    # 1) 최초 적재: 동일 지문 2건은 dup_seq 0/1 로 구별
    load([A, A_DUP, B])
    merge()
    first = state()
    assert [(r[0], r[1], r[2], r[3]) for r in first] == [
        ("다른단지", 0, 1, True),
        ("테스트단지", 0, 1, True),
        ("테스트단지", 1, 1, True),
    ]

    # 2) 같은 입력으로 3번 더 MERGE → 결과 불변 (NFR-01 멱등)
    for _ in range(3):
        force_reload()
        merge()
        assert state() == first

    # 3) A 한 건 해제 + B 사라짐 + D 신규
    load([A_CANCELLED, A_DUP, D])
    merge()
    rows = state()
    by = {(r[0], r[1], r[2]): r for r in rows}
    # B: 삭제되지 않고 missing_since 표시 (FR-204)
    assert by[("다른단지", 0, 1)][3] is True and by[("다른단지", 0, 1)][6] is True
    # D: 신규 version 1
    assert by[("새단지", 0, 1)][3] is True
    # 테스트단지: 속성 해시 정렬상 해제 건의 순번이 정해지고, 바뀐 쪽은 이전 버전 닫힘 + 새 버전
    current = [r for r in rows if r[0] == "테스트단지" and r[3]]
    closed = [r for r in rows if r[0] == "테스트단지" and not r[3]]
    assert len(current) == 2 and sum(r[4] for r in current) == 1
    assert closed and all(r[5] for r in closed)  # valid_to 설정
    assert all(r[2] == 2 for r in current if r[4])  # 해제된 건은 version 2

    # 4) B 다시 나타남 → missing 해제, 버전은 그대로
    load([A_CANCELLED, A_DUP, D, B])
    merge()
    b = [r for r in state() if r[0] == "다른단지"]
    assert len(b) == 1 and b[0][3] and not b[0][6] and b[0][2] == 1

    # 5) 같은 입력 재반영 → 불변
    snap = state()
    force_reload()
    merge()
    assert state() == snap


def test_budget_reserve_is_atomic_under_concurrency():
    """100 스레드가 동시에 1회씩 차감해도 상한(50)을 절대 넘지 않는다 (FR-103: 한도 80% 초과 0)."""
    import datetime as dt
    from concurrent.futures import ThreadPoolExecutor

    import redis

    from aptlake_pipeline.budget import Budget, BudgetExhausted
    from aptlake_pipeline.config import settings

    s = settings()
    r = redis.Redis.from_url(str(s.redis_url))
    b = Budget(r, str(s.pg_dsn), "itest", daily_limit=100, cap=50)
    day = dt.date(2099, 1, 1)
    r.delete(f"budget:itest:{day.isoformat()}")

    def one(_):
        try:
            b.reserve(1, "backfill", 50, day=day)
            return 1
        except BudgetExhausted:
            return 0

    with ThreadPoolExecutor(max_workers=20) as ex:
        ok = sum(ex.map(one, range(100)))
    assert ok == 50 and b.snapshot(day)["used"] == 50
    r.delete(f"budget:itest:{day.isoformat()}")
