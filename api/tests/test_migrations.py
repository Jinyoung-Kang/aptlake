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
