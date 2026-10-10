"""의존성 회귀: Dagster 실행 저장소가 쓰는 PostgreSQL 드라이버.

SQLAlchemy 2.1 은 postgresql:// 의 기본 드라이버를 psycopg(3)으로 바꿨다. 그 드라이버는 서버측 바인딩이라
dagster-postgres 의 `NOTIFY run_events, %(notify_id)s` 가 문법 오류가 되고, 실행이 하나도 시작되지 못한다
(2026-10 재배포 때 실제로 일어남 — Dependabot 이 상한 <2.2 안에서 2.1.1 로 올렸다).
"""

from sqlalchemy.engine import make_url


def test_dagster_storage_url_uses_psycopg2_driver():
    assert make_url("postgresql://u@h/d").get_dialect().driver == "psycopg2"
