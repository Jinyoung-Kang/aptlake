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
    for t in ("bronze.rtms_raw", "stage.apt_trade", "silver.apt_trade", "silver.apt_complex",
              "bronze.rtms_rent_raw", "stage.apt_rent", "silver.apt_rent"):  # fmt: skip
        execute(f"DELETE FROM lake.{t} WHERE sgg_cd = '{SGG}'")
    with ops_db.conn() as c:
        c.execute("DELETE FROM ops.ingest_partition WHERE sgg_cd = %s", (SGG,))
        c.execute("DELETE FROM ops.dq_result WHERE partition = %s", (YM,))
        # pipeline 역할에는 month_state 삭제 권한이 없다(최소 권한) → 합성 달만 지우는 함수로 (V007)
        c.execute("SELECT ops.forget_synthetic_month(%s)", (YM,))


_seq = {"n": 0}


def load(items: list[dict], source: str = "rtms", bronze: str = "bronze.rtms_raw") -> None:
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
    catalog().load_table(bronze).append(pa.Table.from_pylist(rows, schema=BRONZE_SCHEMA))
    with ops_db.conn() as c:
        c.execute(
            """UPDATE ops.ingest_partition SET status='LOADED', last_ingest_id=%s, fetch_count=fetch_count+1,
                     rows_last=%s WHERE source=%s AND sgg_cd=%s AND deal_ym=%s""",
            (iid, len(items), source, SGG, YM),
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


def v2(r: dict, seq: str) -> dict:
    """같은 거래를 상세판(TradeDev)으로 받은 행: 공통 20개 항목은 그대로 + 보강 항목 (ADR-049)."""
    return {**r, "aptSeq": seq, "umdCd": "10100", "landCd": "1", "bonbun": "0001", "bubun": "0001",
            "roadNm": "테스트로", "roadNmSggCd": SGG, "roadNmCd": "1234567", "roadNmSeq": "01", "roadNmbCd": "0",
            "roadNmBonbun": "00001", "roadNmBubun": "00000"}  # fmt: skip


def test_tradedev_recollection_fills_enrichment_without_new_versions(env):
    """같은 파티션을 기본판(v1) → 상세판(v2)으로 다시 받으면: 새 버전 0건, 거래 ID 그대로, 보강 열·단지 일련번호가 채워진다."""
    from aptlake_pipeline import ops_db
    from aptlake_pipeline.lake import execute

    _cleanup()  # 앞 시험이 남긴 합성 파티션 상태에서 시작하지 않게
    with ops_db.conn() as c:
        c.execute("INSERT INTO ops.ingest_partition (sgg_cd, deal_ym) VALUES (%s, %s)", (SGG, YM))
    load([A, A_DUP, B])
    merge()
    before = execute(f"SELECT trade_id, version, attr_hash FROM lake.silver.apt_trade WHERE sgg_cd = '{SGG}'")
    load([v2(A, f"{SGG}-1"), v2(A_DUP, f"{SGG}-1"), v2(B, f"{SGG}-2")])
    s = merge()
    after = execute(f"""SELECT trade_id, version, attr_hash, apt_seq, road_nm, is_current
                        FROM lake.silver.apt_trade WHERE sgg_cd = '{SGG}'""")
    assert sorted(r[:3] for r in after) == sorted(before)  # 버전·거래 ID·속성 해시 그대로
    assert all(r[3] and r[4] == "테스트로" and r[5] for r in after)
    cx = dict(execute(f"SELECT apt_nm, apt_seq FROM lake.silver.apt_complex WHERE sgg_cd = '{SGG}'"))
    assert cx == {"테스트단지": f"{SGG}-1", "다른단지": f"{SGG}-2"}
    assert {c["name"]: c for c in s.checks}["complex_aptseq_conflicts"]["passed"]

    # 일련번호가 차원과 다르게 오면 바꾸지 않고 경고로 센다
    load([v2(A, f"{SGG}-9"), v2(A_DUP, f"{SGG}-9"), v2(B, f"{SGG}-2")])
    s = merge()
    check = {c["name"]: c for c in s.checks}["complex_aptseq_conflicts"]
    assert not check["passed"] and not check["blocking"] and check["metric"]["conflicts"] == 1
    assert (
        dict(execute(f"SELECT apt_nm, apt_seq FROM lake.silver.apt_complex WHERE sgg_cd = '{SGG}'"))["테스트단지"]
        == f"{SGG}-1"
    )


def rent(deposit="30,000", monthly="0", floor="5", ctype="신규", day="10") -> dict:
    return {
        "sggCd": SGG, "umdNm": "테스트동", "aptNm": "테스트단지", "jibun": "1-1", "excluUseAr": "84.9",
        "dealYear": "2099", "dealMonth": "1", "dealDay": day, "deposit": deposit, "monthlyRent": monthly,
        "floor": floor, "buildYear": "2000", "contractTerm": "99.01~01.01", "contractType": ctype,
        "useRRRight": " ", "preDeposit": " ", "preMonthlyRent": " ", "aptSeq": f"{SGG}-1", "roadnm": "테스트로 1",
        "roadnmsggcd": SGG, "roadnmcd": "1234567", "roadnmseq": "1", "roadnmbcd": "0", "roadnmbonbun": "00001",
        "roadnmbubun": "00000",
    }  # fmt: skip


def test_rent_scd2_lifecycle_uses_the_same_merge(env):
    """전월세(ADR-050): 매매와 같은 한 문장 MERGE — 같은 지문 계약 dup_seq, 재반영 불변, 계약 구분 변경 → 새 버전,
    사라짐 → missing. 매매 표·상태는 건드리지 않는다."""
    from aptlake_pipeline import ops_db, silver
    from aptlake_pipeline.lake import execute

    _cleanup()
    with ops_db.conn() as c:
        c.execute("INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES ('rent', %s, %s)", (SGG, YM))

    def rent_merge():
        s = silver.merge_month(YM, run_id="itest", spec=silver.RENT_MERGE)
        assert all(c["passed"] for c in s.checks if c["blocking"]), s.checks
        return s

    def rows():
        return execute(f"""SELECT deposit_manwon, monthly_rent_manwon, dup_seq, version, is_current, contract_type,
                                  missing_since IS NOT NULL, apt_seq
                           FROM lake.silver.apt_rent WHERE sgg_cd = '{SGG}' ORDER BY 1, 2, 3, 4""")

    load([rent(), rent(), rent("1,000", "50")], "rent", "bronze.rtms_rent_raw")
    rent_merge()
    first = rows()
    assert [r[:5] for r in first] == [(1000, 50, 0, 1, True), (30000, 0, 0, 1, True), (30000, 0, 1, 1, True)]
    assert all(r[7] == f"{SGG}-1" for r in first)
    with ops_db.conn() as c:
        c.execute("UPDATE ops.ingest_partition SET status='LOADED' WHERE source='rent' AND sgg_cd=%s", (SGG,))
    rent_merge()
    assert rows() == first  # 같은 입력 재반영 → 불변

    load([rent(ctype="갱신"), rent()], "rent", "bronze.rtms_rent_raw")  # 한 건 계약 구분 바뀜, 월세 계약은 사라짐
    s = rent_merge()
    cur = [r for r in rows() if r[4]]
    assert sorted((r[0], r[3], r[5], r[6]) for r in cur) == [(1000, 1, "신규", True), (30000, 1, "신규", False),
                                                           (30000, 2, "갱신", False)]  # fmt: skip
    assert {c["name"] for c in s.checks} == {"silver_no_duplicate_current_keys", "silver_matches_stage_per_partition",
                                             "missing_rows_under_3_observations", "dup_fingerprint_groups"}  # fmt: skip
    assert execute(f"SELECT count(*) FROM lake.silver.apt_trade WHERE sgg_cd = '{SGG}'")[0][0] == 0
    assert ops_db.month_needs_publish(YM, source="rent") and not ops_db.month_needs_publish(YM)


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
