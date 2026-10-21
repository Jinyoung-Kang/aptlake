"""발행 기록 SQL — 실제 PostgreSQL(운영과 같은 마이그레이션, pipeline 역할 권한)로.

달 발행 때 버전·달 스냅숏 파일·수집→발행 지연(L3)이 한 트랜잭션으로 남고(ADR-044·046),
지연은 직전 기록 발행 이후 새로 수집된 MERGED 파티션만 센다.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row
from testcontainers.community.postgres import PostgresContainer

from aptlake_pipeline import ops_db, publish
from aptlake_pipeline.snapshots import MonthFile

ROOT = Path(__file__).resolve().parents[2]
PW = "test-only-password"


@pytest.fixture(scope="module")
def pipeline_dsn():
    with PostgresContainer("postgres:16-alpine", username="postgres", password=PW, dbname="aptlake") as pg:
        su = f"postgresql://postgres:{PW}@{pg.get_container_host_ip()}:{pg.get_exposed_port(5432)}/aptlake"
        with psycopg.connect(su, autocommit=True) as c:
            for role in ("pipeline", "api_app", "migrator"):
                c.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{PW}'")
            for f in sorted((ROOT / "api/migrations").glob("V*.sql")):
                c.execute(f.read_text())
            c.execute(
                """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, last_changed_at) VALUES
                   ('11110', '202609', 'MERGED', now() - interval '1 day'),
                   ('11140', '202609', 'MERGED', now() - interval '1 day'),
                   ('11170', '202609', 'LOADED', now() - interval '1 day')"""
            )
        yield su.replace(f"postgres:{PW}", f"pipeline:{PW}"), su


@pytest.fixture
def use_pg(monkeypatch, pipeline_dsn):
    dsn, _ = pipeline_dsn

    @contextlib.contextmanager
    def conn():
        with psycopg.connect(dsn, row_factory=dict_row) as c:
            yield c

    monkeypatch.setattr(ops_db, "conn", conn)
    return pipeline_dsn[1]


def mf(ym: str, pid: str) -> MonthFile:
    return MonthFile(ym, f"trade/deal_ym={ym}/{pid}.parquet", 10, 100, "a" * 64)


def test_month_publish_records_files_and_lag_only_for_newly_collected_partitions(use_pg):
    base, p2 = "f0000000-0000-0000-0000-000000000001", "f0000000-0000-0000-0000-000000000002"
    ops_db.record_dataset_version("gold@t.1", ops_db_now(use_pg), ["202609"], {}, {}, None, base, [mf("202609", base)],
                                  baseline=True)  # fmt: skip
    with psycopg.connect(use_pg, autocommit=True) as c:  # 기준선 뒤 11110 만 새로 수집 (11170 은 검사에 막혀 LOADED)
        c.execute(
            "UPDATE ops.ingest_partition SET last_changed_at = clock_timestamp() WHERE sgg_cd IN ('11110','11170')"
        )
    assert ops_db.last_month_file("202609") == mf("202609", base).object_key
    ops_db.record_dataset_version("gold@t.2", ops_db_now(use_pg), ["202609"], {}, {}, None, p2, [mf("202609", p2)])
    with psycopg.connect(use_pg) as c:
        lag = c.execute("SELECT sgg_cd, published_at > fetched_at FROM ops.publish_lag WHERE publish_id = %s", (p2,))
        assert lag.fetchall() == [("11110", True)]
        assert c.execute("SELECT count(*) FROM ops.publish_lag WHERE publish_id = %s", (base,)).fetchone() == (0,)
    assert ops_db.last_month_file("202609") == mf("202609", p2).object_key


def test_record_is_all_or_nothing(use_pg):
    pid = "f0000000-0000-0000-0000-000000000003"
    with pytest.raises(psycopg.errors.UniqueViolation):  # 같은 달 파일 두 번 → 버전 기록도 남지 않는다
        ops_db.record_dataset_version("gold@t.3", ops_db_now(use_pg), ["202609"], {}, {}, None, pid,
                                      [mf("202609", pid), mf("202609", pid)])  # fmt: skip
    with psycopg.connect(use_pg) as c:
        assert c.execute("SELECT count(*) FROM ops.dataset_version WHERE version = 'gold@t.3'").fetchone() == (0,)


def test_sources_have_separate_state_machines(use_pg):
    """원천 차원(V013): 같은 (시군구, 계약월)이라도 매매·전월세 상태·발행 대기는 따로다."""
    ops_db.ensure_partitions("202501", ["11110"])
    ops_db.ensure_partitions("202501", ["11110"], source="rent")
    ops_db.mark_loaded("202501", "11110", rows=3, sha="b" * 64, ingest_id="rent-iid", fetched_at=ops_db_now(use_pg),
                       schema_ver=1, source="rent")  # fmt: skip
    assert [p["sgg_cd"] for p in ops_db.loaded_partitions("202501", source="rent")] == ["11110"]
    assert ops_db.loaded_partitions("202501") == []  # 매매는 그대로 PENDING
    assert ops_db.partition_states("202501")["11110"].status == "PENDING"
    ops_db.mark_month_merged("202501", 7, source="rent")
    assert ops_db.month_needs_publish("202501", source="rent") and not ops_db.month_needs_publish("202501")
    assert "202501" in ops_db.months_needing_publish("rent") and "202501" not in ops_db.months_needing_publish()
    ops_db.mark_month_published("202501", "gold@t.9", source="rent")
    assert not ops_db.month_needs_publish("202501", source="rent")
    with psycopg.connect(use_pg) as c:
        assert c.execute(
            "SELECT schema_ver FROM ops.ingest_partition WHERE source='rent' AND sgg_cd='11110' AND deal_ym='202501'"
        ).fetchone() == (1,)


def test_rent_publish_does_not_count_as_trade_publish_for_lag(use_pg):
    """전월세 달 발행(source='rent')이 같은 달을 '발행'한 것으로 보이면 매매 수집→발행 지연(L3)이 빠진다."""
    base, rent, p2 = (f"f0000000-0000-0000-0000-00000000001{i}" for i in range(3))
    with psycopg.connect(use_pg, autocommit=True) as c:
        c.execute("""INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, last_changed_at)
                     VALUES ('11110', '202608', 'MERGED', now() - interval '1 day')""")
    ops_db.record_dataset_version("gold@t.10", ops_db_now(use_pg), ["202608"], {}, {}, None, base, [mf("202608", base)],
                                  baseline=True)  # fmt: skip
    with psycopg.connect(use_pg, autocommit=True) as c:  # 기준선 뒤 새로 수집
        c.execute("UPDATE ops.ingest_partition SET last_changed_at = clock_timestamp() WHERE deal_ym = '202608'")
    ops_db.record_dataset_version("gold@t.11", ops_db_now(use_pg), ["202608"], {}, {}, None, rent, [], source="rent")
    ops_db.record_dataset_version("gold@t.12", ops_db_now(use_pg), ["202608"], {}, {}, None, p2, [mf("202608", p2)])
    with psycopg.connect(use_pg) as c:
        assert c.execute("SELECT sgg_cd FROM ops.publish_lag WHERE publish_id = %s", (p2,)).fetchall() == [("11110",)]
        assert c.execute("SELECT source FROM ops.dataset_version WHERE version = 'gold@t.11'").fetchone() == ("rent",)


def test_commit_version_writes_data_as_of_per_source(use_pg, monkeypatch):
    """데이터 기준 시각은 원천별 키에 (al:ds:asof:trade·rent). 예전엔 키 하나라 전월세 발행이 매매 기준 시각을 덮었고,
    버전이 오를 때 시각이 뒤로 가기도 했다. 어느 원천이 발행하든 두 키를 발행 기록의 원천별 최댓값으로 다시 맞춘다."""
    store: dict[str, str] = {"al:ds:asof": "옛 전역 값"}
    fake = SimpleNamespace(mset=store.update, delete=lambda *keys: [store.pop(k, None) for k in keys])
    monkeypatch.setattr(publish, "redis", SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _url: fake)))
    monkeypatch.setattr(publish, "settings", lambda: SimpleNamespace(redis_url="redis://test"))
    monkeypatch.setattr(publish, "current_snapshot_id", lambda _t: 1)
    t0, t1, t_rent = (dt.datetime(2099, 1, d, 3, tzinfo=dt.UTC) for d in (1, 2, 3))  # 앞 시험의 기록(지금)보다 늦게
    with psycopg.connect(use_pg, autocommit=True) as c:  # 이 시험만의 수집 행 (앞 시험이 넣은 행에 기대지 않는다)
        c.execute("""INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES ('rtms', '11110', '209912'),
                     ('rent', '11110', '209912') ON CONFLICT DO NOTHING""")

    def fetched(source: str, at: dt.datetime) -> None:
        with psycopg.connect(use_pg, autocommit=True) as c:
            n = c.execute(
                "UPDATE ops.ingest_partition SET last_fetched_at = %s WHERE source = %s AND deal_ym = '209912'",
                (at, source),
            ).rowcount
            assert n == 1

    def asof() -> dict[str, dt.datetime]:
        return {
            k.rsplit(":", 1)[1]: dt.datetime.fromisoformat(v) for k, v in store.items() if k.startswith("al:ds:asof:")
        }

    fetched("rtms", t1)
    fetched("rent", t_rent)
    publish.commit_version("gold@asof.1", ["202609"], {}, None, "f0000000-0000-0000-0000-000000000021")
    assert "al:ds:asof" not in store and store["al:ds:ver"] == "gold@asof.1"
    assert asof()["trade"] == t1
    publish.commit_version("gold@asof.2", ["202609"], {}, None, "f0000000-0000-0000-0000-000000000022", source="rent")
    assert asof() == {"trade": t1, "rent": t_rent} and store["al:ds:ver"] == "gold@asof.2"  # 매매 값은 그대로
    fetched("rtms", t0)  # 이른 기준으로 다시 발행해도 뒤로 가지 않는다
    publish.commit_version("gold@asof.3", [], {}, None, "f0000000-0000-0000-0000-000000000023")
    assert asof() == {"trade": t1, "rent": t_rent} and store["al:ds:ver"] == "gold@asof.3"
    assert ops_db.data_as_of_by_source() == {"trade": t1, "rent": t_rent}

    # 배포 직후(옛 파이프라인은 전역 키만 썼다): 발행 없이 원천별 키를 기록에서 되살린다. 버전은 그대로, 옛 키는 남긴다
    # (아직 옛 API 가 읽을 수 있어서 — 다음 발행이 지운다)
    store.clear()
    store.update({"al:ds:ver": "gold@asof.3", "al:ds:asof": "옛 전역 값"})
    assert publish.sync_data_as_of() == {"al:ds:asof:trade": t1.isoformat(), "al:ds:asof:rent": t_rent.isoformat()}
    assert asof() == {"trade": t1, "rent": t_rent}
    assert store["al:ds:ver"] == "gold@asof.3" and store["al:ds:asof"] == "옛 전역 값"


def test_sync_data_as_of_without_any_publish_writes_nothing(monkeypatch):
    """첫 기동(발행 기록 없음): 쓸 키가 없다 — 빈 MSET 은 Redis 오류라 부르지 않는다."""
    monkeypatch.setattr(publish.ops_db, "data_as_of_by_source", lambda: {})
    monkeypatch.setattr(publish, "redis", SimpleNamespace(Redis=SimpleNamespace(from_url=lambda _url: pytest.fail())))
    monkeypatch.setattr(publish, "settings", lambda: SimpleNamespace(redis_url="redis://test"))
    assert publish.sync_data_as_of() == {}


# ───── 재시도 사유: FETCHING 으로 남은 파티션을 사유 없이 RETRY 로 두지 않는다 ─────


def _partitions(su: str, deal_ym: str, source: str = "rent") -> dict[str, tuple]:
    with psycopg.connect(su) as c:
        rows = c.execute(
            "SELECT sgg_cd, status, attempts, last_error FROM ops.ingest_partition WHERE source = %s AND deal_ym = %s",
            (source, deal_ym),
        ).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


def _due_in(su: str, deal_ym: str, source: str = "rent") -> dict[str, dt.timedelta]:
    """시군구별 다음 기한까지 남은 시간 — 0 이하면 이미 기한 (센서 기한 질의: RETRY 는 next_due_at <= now() 일 때 다시 잡는다)."""
    with psycopg.connect(su) as c:
        rows = c.execute(
            "SELECT sgg_cd, next_due_at - now() FROM ops.ingest_partition WHERE source = %s AND deal_ym = %s",
            (source, deal_ym),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def test_reset_fetching_records_why_the_partition_is_retried(use_pg):
    """앞 실행이 정리 없이 끝나 FETCHING 으로 남은 파티션: 다음 실행이 '중단된 실행' 사유와 함께 RETRY 로 되돌린다.
    사유 없이 되돌리면 수집 상태 화면에 빈 사유의 재시도가 쌓였다 (2026-10-21 전월세 446곳). 다른 상태·원천은 그대로."""
    ops_db.ensure_partitions("202211", ["11110", "11140", "11170"], source="rent")
    ops_db.ensure_partitions("202211", ["11110"])
    for sgg in ("11110", "11140"):
        ops_db.mark_fetching("202211", sgg, source="rent")
    ops_db.mark_fetching("202211", "11110")  # 매매 쪽 같은 파티션
    ops_db.mark_failed("202211", "11140", "HTTP 503", source="rent")  # 이 실행 안에서 실패 사유가 이미 남은 곳
    assert ops_db.reset_fetching("202211", source="rent") == 1
    assert _partitions(use_pg, "202211") == {
        "11110": ("RETRY", 0, ops_db.INTERRUPTED),
        "11140": ("RETRY", 1, "HTTP 503"),
        "11170": ("PENDING", 0, None),
    }
    assert _partitions(use_pg, "202211", source="rtms") == {"11110": ("FETCHING", 0, None)}
    assert _due_in(use_pg, "202211")["11110"] <= dt.timedelta(0)  # 실행 시작의 되돌림은 기한을 건드리지 않는다
    assert ops_db.reset_fetching("202211", source="rent") == 0  # 멱등


def test_reset_after_failed_run_pushes_the_due_time_back(use_pg):
    """실행 실패 경로(retry_after): 되돌린 파티션의 기한을 1시간 뒤로. 백필·재확인 파티션은 기한이 이미 지나 있어 그대로 두면
    센서 다음 틱(5분)이 같은 달을 다시 받는다 — 적재 단계가 계속 실패하면 attempts 0 인 채 5분마다 예산을 쓴다.
    원래 기한이 더 뒤인 파티션(수동 실행이 기한 전 파티션까지 받은 경우)도 1시간 뒤 — 받은 원천을 적재하지 못했으니."""
    ops_db.ensure_partitions("202212", ["11110", "11140", "11170"], source="rent")
    with psycopg.connect(use_pg, autocommit=True) as c:
        c.execute("""UPDATE ops.ingest_partition
                     SET next_due_at = now() + CASE sgg_cd WHEN '11110' THEN interval '-30 days' ELSE interval '60 days' END
                     WHERE source = 'rent' AND deal_ym = '202212'""")
    for sgg in ("11110", "11140"):
        ops_db.mark_fetching("202212", sgg, source="rent")
    after = ops_db.FAILED_RUN_RETRY_AFTER
    assert ops_db.reset_fetching("202212", source="rent", reason="run failed: X", retry_after=after) == 2
    due = _due_in(use_pg, "202212")
    for sgg in ("11110", "11140"):
        assert dt.timedelta(minutes=59) < due[sgg] <= dt.timedelta(hours=1), sgg
    assert due["11170"] > dt.timedelta(days=59)  # 수집 중이 아니던 곳은 그대로
    assert _partitions(use_pg, "202212")["11110"] == ("RETRY", 0, "run failed: X")


def test_failed_run_leaves_its_failure_reason_on_partitions_it_was_fetching(use_pg, monkeypatch):
    """원천 응답은 받았는데 bronze 적재가 실패한 실행(2026-10-21: MinIO 시간 초과로 실행 두 개 실패): 그 실행이 수집 중으로
    둔 파티션을 실패 사유와 함께 RETRY 로 되돌린다 — 다음 실행이 예산 상한에 걸려 다시 받지 못해도 사유가 보인다."""
    from aptlake_pipeline import ingest
    from aptlake_pipeline.rtms.client import FetchFailed, FetchResult
    from aptlake_pipeline.rtms.parse import parse_page

    xml = (Path(__file__).parent / "fixtures/rtms_dev_11110_202407.xml").read_text()

    class Client:
        def __init__(self, *_a, **_k):
            pass

        async def fetch(self, sgg: str, ym: str) -> FetchResult:
            if sgg == "11140":
                raise FetchFailed("HTTP 503")
            return FetchResult(sgg, ym, [xml.encode()], [parse_page(xml)], 1.8e9, 1)

        async def aclose(self) -> None:
            pass

    class Archive:
        def __init__(self, _prefix: str):
            pass

        def put(self, _r) -> str:
            return "s3://raw/test/"

        def items(self, *_a):
            return None

    def load_table(_name):
        raise OSError("AWS Error NETWORK_CONNECTION during CreateMultipartUpload operation: curlCode: 28")

    monkeypatch.setattr(ingest, "settings", lambda: SimpleNamespace(
        data_go_kr_key="test", fetch_tps=100.0, fetch_concurrency=2, rtms_trade_url="http://source.test"))  # fmt: skip
    monkeypatch.setattr(ingest, "RtmsClient", Client)
    monkeypatch.setattr(ingest, "RawArchive", Archive)
    monkeypatch.setattr(ingest, "catalog", lambda: SimpleNamespace(load_table=load_table))
    monkeypatch.setattr(ingest, "APPEND_BACKOFF_S", 0.0)
    with pytest.raises(OSError, match="NETWORK_CONNECTION"):
        ingest.ingest_month("202407", ["11110", "11140"], reserve=lambda _n: None)
    state = _partitions(use_pg, "202407", source="rtms")
    assert state["11110"][:2] == ("RETRY", 0)  # 파티션 결함이 아니라 재시도 횟수는 그대로
    assert state["11110"][2].startswith("run failed: OSError: AWS Error NETWORK_CONNECTION")
    assert state["11140"] == ("RETRY", 1, "HTTP 503")  # 수집 실패는 그 사유 그대로 (덮지 않는다)
    # 기한도 뒤로 — 곧바로 기한이면 센서가 다음 틱(5분)에 같은 달을 다시 받아, 계속 실패하는 적재가 5분마다 예산을 쓴다
    due = _due_in(use_pg, "202407", source="rtms")
    assert dt.timedelta(minutes=59) < due["11110"] <= dt.timedelta(hours=1)
    # 수집 실패는 mark_failed 의 백오프(30분 × 횟수) 그대로
    assert dt.timedelta(minutes=29) < due["11140"] <= dt.timedelta(minutes=30)


# ───── 반영 기록(merged_ingest_id, V014): 반영 전 적재본은 어떤 되돌림을 거쳐도 같은 원본 재수집에서 LOADED ─────


def _merge_state(su: str, deal_ym: str, sgg: str = "11110") -> tuple:
    with psycopg.connect(su) as c:
        return c.execute(
            """SELECT status, last_ingest_id, merged_ingest_id FROM ops.ingest_partition
               WHERE source = 'rtms' AND sgg_cd = %s AND deal_ym = %s""",
            (sgg, deal_ym),
        ).fetchone()


@pytest.mark.parametrize(
    ("deal_ym", "detour"),
    [("202301", "run start"), ("202302", "failed run"), ("202303", "budget"), ("202304", "fetch failed")],
)
def test_unmerged_load_stays_loaded_when_the_same_payload_comes_back(use_pg, deal_ym, detour):
    """silver 반영 전(LOADED) 적재본이 수집 중 되돌리기·예산 소진·수집 실패로 RETRY 를 거친 뒤 같은 원본을 다시 받으면
    LOADED 로 남아 다음 반영 대상이어야 한다. 예전엔 직전 상태(RETRY)만 보고 MERGED 로 넘겨 그 적재본이 silver 에 들어가지
    않았다. 반영한 뒤의 같은 원본은 MERGED — 반영 여부는 상태값이 아니라 기록(merged_ingest_id)으로 정한다."""
    x = f"{deal_ym}-11110-20261021T000000-cccccccc"
    ops_db.ensure_partitions(deal_ym, ["11110"])
    ops_db.mark_loaded(deal_ym, "11110", rows=3, sha="c" * 64, ingest_id=x, fetched_at=ops_db_now(use_pg))

    def detour_to_retry() -> None:
        ops_db.mark_fetching(deal_ym, "11110")
        if detour == "run start":  # 앞 실행이 정리 없이 끝난 흔적
            ops_db.reset_fetching(deal_ym)
        elif detour == "failed run":  # 이 실행의 실패 정리 (예: 앞 순번 시군구의 원본 보관 실패)
            ops_db.reset_fetching(deal_ym, reason="run failed: OSError", retry_after=ops_db.FAILED_RUN_RETRY_AFTER)
        elif detour == "budget":
            ops_db.mark_failed(deal_ym, "11110", "budget: exhausted")
            ops_db.release_attempt(deal_ym, "11110")
        else:
            ops_db.mark_failed(deal_ym, "11110", "HTTP 503")
        assert _merge_state(use_pg, deal_ym)[0] == "RETRY"

    detour_to_retry()
    ops_db.mark_unchanged(deal_ym, "11110", ops_db_now(use_pg))
    assert _merge_state(use_pg, deal_ym) == ("LOADED", x, None)
    assert [p["sgg_cd"] for p in ops_db.loaded_partitions(deal_ym)] == ["11110"]

    ops_db.mark_merged(deal_ym, ["11110"])
    assert _merge_state(use_pg, deal_ym) == ("MERGED", x, x)
    detour_to_retry()
    ops_db.mark_unchanged(deal_ym, "11110", ops_db_now(use_pg))
    assert _merge_state(use_pg, deal_ym) == ("MERGED", x, x)  # 반영한 적재본 그대로 → 다시 반영하지 않는다
    assert ops_db.loaded_partitions(deal_ym) == []

    y = f"{deal_ym}-11110-20261022T000000-dddddddd"  # 새 원본 적재 → 반영 전이라 같은 원본을 다시 받아도 LOADED
    ops_db.mark_loaded(deal_ym, "11110", rows=4, sha="d" * 64, ingest_id=y, fetched_at=ops_db_now(use_pg))
    ops_db.mark_unchanged(deal_ym, "11110", ops_db_now(use_pg))
    assert _merge_state(use_pg, deal_ym) == ("LOADED", y, x)


def test_failed_run_then_same_payload_keeps_the_unmerged_load_for_silver(use_pg, monkeypatch):
    """실제 수집 경로: 11140 은 반영 전 적재본(LOADED)인데, 앞 순번 11110 의 원본 보관이 실패해 실행이 끝나면 실패 정리가
    11140 을 RETRY 로 되돌린다. 다음 실행이 같은 원본을 받으면 11140 은 LOADED — silver 반영 대상에 남는다."""
    from aptlake_pipeline import ingest
    from aptlake_pipeline.rtms.client import FetchResult

    ym, same = "202305", b"<response>same</response>"
    unmerged = FetchResult("11140", ym, [same], [], 1.8e9, 1)
    ops_db.ensure_partitions(ym, ["11110", "11140"])
    ops_db.mark_loaded(
        ym, "11140", rows=0, sha=unmerged.sha256, ingest_id=f"{ym}-11140-x", fetched_at=ops_db_now(use_pg)
    )

    class Client:
        def __init__(self, *_a, **_k):
            pass

        async def fetch(self, sgg: str, deal_ym: str) -> FetchResult:
            return (
                unmerged if sgg == "11140" else FetchResult(sgg, deal_ym, [b"<response>new</response>"], [], 1.8e9, 1)
            )

        async def aclose(self) -> None:
            pass

    class Archive:
        def __init__(self, _prefix: str):
            pass

        def put(self, _r) -> str:
            raise OSError("AWS Error NETWORK_CONNECTION during PutObject operation: curlCode: 28")

    monkeypatch.setattr(ingest, "settings", lambda: SimpleNamespace(
        data_go_kr_key="test", fetch_tps=100.0, fetch_concurrency=2, rtms_trade_url="http://source.test"))  # fmt: skip
    monkeypatch.setattr(ingest, "RtmsClient", Client)
    monkeypatch.setattr(ingest, "RawArchive", Archive)
    with pytest.raises(OSError, match="NETWORK_CONNECTION"):
        ingest.ingest_month(ym, ["11110", "11140"], reserve=lambda _n: None)
    assert _merge_state(use_pg, ym, "11140")[0] == "RETRY"

    summary = ingest.ingest_month(ym, ["11140"], reserve=lambda _n: None)
    assert summary.unchanged == ["11140"]
    assert _merge_state(use_pg, ym, "11140") == ("LOADED", f"{ym}-11140-x", None)
    assert [p["sgg_cd"] for p in ops_db.loaded_partitions(ym)] == ["11140"]


def ops_db_now(su: str):
    with psycopg.connect(su) as c:
        return c.execute("SELECT now()").fetchone()[0]
