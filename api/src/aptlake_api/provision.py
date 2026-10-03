"""웹 화면(BFF) 전용 키 등록 — db-migrate 단계에서 migrator 역할로 실행.

WEB_API_KEY(.env, `make init` 이 생성)는 nginx 만 갖고 브라우저에는 가지 않는다.
DB 에는 HMAC 만 저장한다. 키가 바뀌면(순환) 같은 클라이언트의 이전 키는 폐기한다. 멱등.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import psycopg

from .core import keys

CLIENT = "web-ui"
PLAN = "web"
# 웹 화면은 데이터 조회(read)와 수집 상태 보기(ops_read)만 — 로그 비우기·되돌리기는 운영자 키(ops) (QA-001)
WEB_SCOPES = ["read", "ops_read"]


def main() -> int:
    raw = os.environ.get("WEB_API_KEY", "")
    pepper = os.environ.get("API_KEY_PEPPER", "")
    if not raw:
        print("WEB_API_KEY 없음 — 웹 전용 키 등록 건너뜀 (웹 화면은 anonymous 한도를 씀)")
        return 0
    parsed = keys.parse(raw)
    if parsed is None or not pepper:
        print("WEB_API_KEY 형식이 올바르지 않거나 API_KEY_PEPPER 가 없음", file=sys.stderr)
        return 1
    key_id, secret = parsed
    expires = dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=365)
    with psycopg.connect(os.environ["MIGRATOR_DSN"]) as c, c.transaction():
        row = c.execute("SELECT client_id FROM api.client WHERE name=%s", (CLIENT,)).fetchone()
        if row is None:
            row = c.execute(
                "INSERT INTO api.client (name, plan_id) VALUES (%s, %s) RETURNING client_id", (CLIENT, PLAN)
            ).fetchone()
        assert row is not None
        client_id = row[0]
        c.execute(
            """INSERT INTO api.api_key (key_id, client_id, secret_hmac, scopes, expires_at)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (key_id) DO UPDATE SET secret_hmac = EXCLUDED.secret_hmac, scopes = EXCLUDED.scopes,
                 expires_at = EXCLUDED.expires_at, revoked_at = NULL""",
            (key_id, client_id, keys.hmac_secret(pepper, secret), WEB_SCOPES, expires),
        )
        revoked = c.execute(
            """UPDATE api.api_key SET revoked_at = now()
               WHERE client_id = %s AND key_id <> %s AND revoked_at IS NULL RETURNING key_id""",
            (client_id, key_id),
        ).fetchall()
        c.execute(
            """INSERT INTO api.audit_log (actor, action, target, detail)
                     VALUES ('provision', 'web_key.ensure', %s, jsonb_build_object('rotated', %s::int))""",
            (key_id, len(revoked)),
        )
    print(f"web key ensured ({key_id[:4]}…), rotated out {len(revoked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
