"""마이그레이션이 만든 권한 경계 (Testcontainers, 운영과 같은 마이그레이션)."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "api/migrations"


def test_pipeline_can_forget_only_synthetic_months(stack):
    """통합 테스트 정리용 함수: 합성 달(2099xx)만 지운다, pipeline 역할은 표 DELETE 없이 이 함수만 쓴다."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        c.execute(
            "INSERT INTO ops.month_state (deal_ym, needs_publish) VALUES ('209901', false) ON CONFLICT DO NOTHING"
        )
        assert c.execute("SELECT ops.forget_synthetic_month('209901')").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM ops.month_state WHERE deal_ym = '209901'").fetchone()[0] == 0
        for real in ("202407", "209913", "2099011"):
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute("SELECT ops.forget_synthetic_month(%s)", (real,))
        privs = c.execute(
            """SELECT has_function_privilege('pipeline', 'ops.forget_synthetic_month(text)', 'EXECUTE'),
                      has_table_privilege('pipeline', 'ops.month_state', 'DELETE'),
                      has_function_privilege('api_app', 'ops.forget_synthetic_month(text)', 'EXECUTE')"""
        ).fetchone()
        assert privs == (True, False, False)


def _pk(c, table: str) -> list[str]:
    return c.execute(
        """SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_index i
           CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
           JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
           WHERE i.indrelid = %s::regclass AND i.indisprimary""",
        (table,),
    ).fetchone()[0]


def test_v013_source_dimension_keys_defaults_and_checks(stack):
    """V013: 원천이 키 앞에 — 같은 (시군구, 계약월)이라도 원천별 상태. 기존 행·원천 생략은 매매, 모르는 원천은 거부."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        assert _pk(c, "ops.ingest_partition") == ["source", "sgg_cd", "deal_ym"]
        assert _pk(c, "ops.month_state") == ["source", "deal_ym"]
        try:
            c.execute("INSERT INTO ops.ingest_partition (sgg_cd, deal_ym) VALUES ('99999', '209902')")
            c.execute("INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES ('rent', '99999', '209902')")
            rows = c.execute(
                "SELECT source, schema_ver FROM ops.ingest_partition WHERE deal_ym = '209902' ORDER BY source"
            ).fetchall()
            assert rows == [("rent", None), ("rtms", None)]  # 생략하면 매매, 판은 적재 전 NULL
            with pytest.raises(psycopg.errors.UniqueViolation):
                c.execute(
                    "INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES ('rent', '99999', '209902')"
                )
            c.execute("INSERT INTO ops.month_state (deal_ym) VALUES ('209902')")
            c.execute("INSERT INTO ops.month_state (source, deal_ym) VALUES ('rent', '209902')")
            assert c.execute("SELECT count(*) FROM ops.month_state WHERE deal_ym = '209902'").fetchone()[0] == 2
            for sql in (
                "INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym) VALUES ('trade', '99999', '209902')",
                "INSERT INTO ops.month_state (source, deal_ym) VALUES ('rtms_rent', '209903')",
                """INSERT INTO ops.dataset_version (version, data_as_of, partitions, snapshots, row_counts, source)
                   VALUES ('gold@v013', now(), '{}', '{}', '{}', 'rtms')""",  # 발행 기록의 원천은 trade·rent
            ):
                with pytest.raises(psycopg.errors.CheckViolation):
                    c.execute(sql)
            c.execute(
                """INSERT INTO ops.dataset_version (version, data_as_of, partitions, snapshots, row_counts)
                   VALUES ('gold@v013', now(), '{}', '{}', '{}')"""
            )
            assert (
                c.execute("SELECT source FROM ops.dataset_version WHERE version = 'gold@v013'").fetchone()[0] == "trade"
            )
        finally:
            c.execute("DELETE FROM ops.ingest_partition WHERE deal_ym = '209902'")
            c.execute("DELETE FROM ops.dataset_version WHERE version = 'gold@v013'")
            c.execute("SELECT ops.forget_synthetic_month('209902')")


def test_forget_synthetic_month_clears_both_sources_as_pipeline(stack):
    """레이크 통합 시험의 합성 달 정리는 매매·전월세 발행 상태를 모두 지운다 — pipeline 역할로, 표 DELETE 권한 없이."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        c.execute(
            """INSERT INTO ops.month_state (source, deal_ym, needs_publish)
               VALUES ('rtms', '209904', true), ('rent', '209904', true), ('rent', '209905', true)"""
        )
        try:
            c.execute("SET ROLE pipeline")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("DELETE FROM ops.month_state WHERE deal_ym = '209904'")
            assert c.execute("SELECT ops.forget_synthetic_month('209904')").fetchone()[0] == 2
            c.execute("RESET ROLE")
            left = c.execute("SELECT source, deal_ym FROM ops.month_state WHERE deal_ym LIKE '2099%'").fetchall()
            assert left == [("rent", "209905")]  # 요청한 달만
        finally:
            c.execute("RESET ROLE")
            c.execute("DELETE FROM ops.month_state WHERE deal_ym LIKE '2099%'")


def test_v013_role_boundaries(stack):
    """API 는 원천 차원 표를 읽기만(전세가율 완결 판단·품질 요약) — 관리자 재시도는 상태 열만 바꾸고 원천·판은 못 바꾼다.
    파이프라인은 발행 상태를 쓰되 지우지 못한다."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        privs = c.execute(
            """SELECT has_table_privilege('api_app', 'ops.ingest_partition', 'SELECT'),
                      has_table_privilege('api_app', 'ops.month_state', 'SELECT'),
                      has_table_privilege('api_app', 'ops.dataset_version', 'SELECT'),
                      has_column_privilege('api_app', 'ops.ingest_partition', 'status', 'UPDATE'),
                      has_column_privilege('api_app', 'ops.ingest_partition', 'source', 'UPDATE'),
                      has_column_privilege('api_app', 'ops.ingest_partition', 'schema_ver', 'UPDATE'),
                      has_table_privilege('api_app', 'ops.ingest_partition', 'INSERT'),
                      has_table_privilege('api_app', 'ops.month_state', 'UPDATE'),
                      has_table_privilege('api_app', 'ops.dataset_version', 'INSERT'),
                      has_column_privilege('pipeline', 'ops.ingest_partition', 'schema_ver', 'UPDATE'),
                      has_table_privilege('pipeline', 'ops.month_state', 'INSERT'),
                      has_table_privilege('pipeline', 'ops.month_state', 'UPDATE'),
                      has_table_privilege('pipeline', 'ops.month_state', 'DELETE'),
                      has_table_privilege('pipeline', 'ops.dataset_version', 'INSERT')"""
        ).fetchone()
        assert privs == (True, True, True, True, False, False, False, False, False, True, True, True, False, True)


def test_v014_merged_ingest_id_backfills_merged_partitions(stack):
    """V014: 파티션이 silver 에 반영한 적재본. 이관 때 이미 MERGED 인 파티션만 마지막 적재본으로 채운다 — 다른 상태는
    반영했는지 기록이 없어 비워 둔다(같은 원본을 다시 받으면 한 번 더 반영, 멱등). 이관 전 상태를 만들려고 빈 DB 에
    V013 까지 적용하고 행을 넣은 뒤 V014 를 적용한다."""
    files = sorted(MIGRATIONS.glob("V*__*.sql"))
    v014 = [f for f in files if f.name.startswith("V014__")]
    assert len(v014) == 1
    db = "v014_check"
    with psycopg.connect(stack["su"], autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {db}")
        c.execute(f"CREATE DATABASE {db}")
    try:
        with psycopg.connect(stack["su"].rsplit("/", 1)[0] + f"/{db}", autocommit=True) as c:
            for f in files[: files.index(v014[0])]:
                c.execute(f.read_text())
            c.execute(
                """INSERT INTO ops.ingest_partition (source, sgg_cd, deal_ym, status, last_ingest_id) VALUES
                   ('rtms', '11110', '202401', 'MERGED', 'iid-merged'),
                   ('rent', '11110', '202401', 'MERGED', 'iid-rent-merged'),
                   ('rtms', '11140', '202401', 'LOADED', 'iid-loaded'),
                   ('rtms', '11170', '202401', 'QUARANTINED', 'iid-quarantined'),
                   ('rtms', '11200', '202401', 'PENDING', NULL)"""
            )
            c.execute(v014[0].read_text())
            rows = c.execute(
                """SELECT source, sgg_cd, status, merged_ingest_id FROM ops.ingest_partition
                   ORDER BY source, sgg_cd"""
            ).fetchall()
            assert rows == [
                ("rent", "11110", "MERGED", "iid-rent-merged"),
                ("rtms", "11110", "MERGED", "iid-merged"),
                ("rtms", "11140", "LOADED", None),  # 반영 전 적재본
                ("rtms", "11170", "QUARANTINED", None),
                ("rtms", "11200", "PENDING", None),
            ]
    finally:
        with psycopg.connect(stack["su"], autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")


def test_v014_role_boundaries(stack):
    """반영 기록은 파이프라인만 쓴다 (V001 표 단위 권한). API 는 읽기만 — 관리자 재시도의 열 단위 UPDATE 에 없다."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        privs = c.execute(
            """SELECT has_column_privilege('pipeline', 'ops.ingest_partition', 'merged_ingest_id', 'SELECT'),
                      has_column_privilege('pipeline', 'ops.ingest_partition', 'merged_ingest_id', 'UPDATE'),
                      has_column_privilege('api_app', 'ops.ingest_partition', 'merged_ingest_id', 'SELECT'),
                      has_column_privilege('api_app', 'ops.ingest_partition', 'merged_ingest_id', 'UPDATE')"""
        ).fetchone()
        assert privs == (True, True, True, False)


def test_umd_alias_is_append_only_for_pipeline(stack):
    """법정동 이름 대응표는 더하기만 한다 — 지우거나 바꾸면 그 이름의 거래 지문이 다시 바뀐다 (V009)."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        privs = c.execute(
            """SELECT has_table_privilege('pipeline', 'ops.umd_alias', 'SELECT'),
                      has_table_privilege('pipeline', 'ops.umd_alias', 'INSERT'),
                      has_table_privilege('pipeline', 'ops.umd_alias', 'UPDATE'),
                      has_table_privilege('pipeline', 'ops.umd_alias', 'DELETE'),
                      has_table_privilege('api_app', 'ops.umd_alias', 'INSERT')"""
        ).fetchone()
        assert privs == (True, True, False, False, False)


def test_month_file_records_are_append_only_except_deleted_at(stack):
    """달별 스냅숏 파일 기록(V010): 파이프라인은 더하고 지운 시각만 채운다, API 는 읽기만 (ADR-044)."""
    with psycopg.connect(stack["su"], autocommit=True) as c:
        privs = c.execute(
            """SELECT has_table_privilege('pipeline', 'ops.dataset_month_file', 'INSERT'),
                      has_table_privilege('pipeline', 'ops.dataset_month_file', 'DELETE'),
                      has_column_privilege('pipeline', 'ops.dataset_month_file', 'deleted_at', 'UPDATE'),
                      has_column_privilege('pipeline', 'ops.dataset_month_file', 'object_key', 'UPDATE'),
                      has_table_privilege('api_app', 'ops.dataset_month_file', 'SELECT'),
                      has_table_privilege('api_app', 'ops.dataset_month_file', 'INSERT')"""
        ).fetchone()
        assert privs == (True, False, True, False, True, False)


def test_no_bulk_reader_account_on_serving_db(stack):
    """대량 내보내기는 달별 스냅숏 파일에서 뽑는다 — 결과 행 상한 없는 ClickHouse 계정을 두지 않는다 (ADR-044)."""
    users = {r[0] for r in stack["ch"].query("SELECT name FROM system.users").result_rows}
    assert "exporter" not in users and {"api_reader", "usage_writer", "publisher"} <= users
