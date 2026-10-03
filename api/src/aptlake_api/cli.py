"""운영 CLI. 최초 관리자 키 발급 (관리 API 자체가 admin 키를 요구하므로 부트스트랩은 CLI 로).

    docker compose exec api-internal python -m aptlake_api.cli bootstrap-admin
    docker compose exec api-internal python -m aptlake_api.cli demo-keys
    docker compose exec api-internal python -m aptlake_api.cli loadtest-key   (1일 만료)
키 원문은 표준출력에 한 번만 출력되고 어디에도 저장되지 않는다.
"""

from __future__ import annotations

import datetime as dt
import sys

import psycopg

from .core import keys
from .core.settings import settings


def _issue(c: psycopg.Connection, client_name: str, plan: str, scopes: list[str], days: int) -> str:
    row = c.execute("SELECT client_id FROM api.client WHERE name=%s", (client_name,)).fetchone()
    if row is None:
        row = c.execute(
            "INSERT INTO api.client (name, plan_id) VALUES (%s, %s) RETURNING client_id", (client_name, plan)
        ).fetchone()
    assert row is not None
    client_id = row[0]
    issued = keys.issue(settings().api_key_pepper.get_secret_value())
    c.execute(
        """INSERT INTO api.api_key (key_id, client_id, secret_hmac, scopes, expires_at)
                 VALUES (%s, %s, %s, %s, %s)""",
        (issued.key_id, client_id, issued.secret_hmac, scopes, dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=days)),
    )
    c.execute(
        """INSERT INTO api.audit_log (actor, action, target, detail) VALUES ('cli', 'key.create', %s,
                 jsonb_build_object('client', %s::text, 'scopes', %s::text[]))""",
        (issued.key_id, client_name, scopes),
    )
    return issued.api_key


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    with psycopg.connect(settings().pg_dsn.get_secret_value()) as c:
        if cmd == "bootstrap-admin":
            # 운영자: 관리(admin) + 데이터 조회(read) + 수집 상태·오류 로그(ops)
            print(_issue(c, "operator", "pro", ["admin", "read", "ops"], 30))
        elif cmd == "loadtest-key":
            # 부하 측정용 (분당 한도 사실상 없음, 1일 만료). 측정이 끝나면 관리 API 로 폐기할 것
            print(_issue(c, "loadtest", "loadtest", ["read"], 1))
        elif cmd == "demo-keys":
            print("free:", _issue(c, "demo-free", "free", ["read"], 90))
            print("pro :", _issue(c, "demo-pro", "pro", ["read", "bulk"], 90))
        else:
            print(__doc__)
            return 2
    print("# 위 키는 다시 표시되지 않습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
