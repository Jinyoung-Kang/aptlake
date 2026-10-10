"""마이그레이션이 만든 권한 경계 (Testcontainers, 운영과 같은 마이그레이션)."""

from __future__ import annotations

import psycopg
import pytest


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
