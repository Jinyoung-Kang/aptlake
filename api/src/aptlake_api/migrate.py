"""SQL 마이그레이션 적용기 (migrator 역할로 실행).

- 파일명 순서(V001__, V002__ …)로 한 번씩 적용, 각 파일은 하나의 트랜잭션
- 적용된 파일의 SHA-256 을 기록하고, 이미 적용된 파일이 바뀌면 중단 (이력 변조 감지)
- advisory lock 으로 동시 실행(여러 컨테이너)에도 한 번만 적용
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(os.environ.get("MIGRATIONS_DIR", Path(__file__).resolve().parents[2] / "migrations"))
LOCK_ID = 7_261_404


def main() -> int:
    dsn = os.environ["MIGRATOR_DSN"]
    files = sorted(MIGRATIONS.glob("V*__*.sql"))
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS public.schema_migrations (
                                version text PRIMARY KEY, sha256 char(64) NOT NULL,
                                applied_at timestamptz NOT NULL DEFAULT now())""")
            rows = conn.execute("SELECT version, sha256 FROM public.schema_migrations").fetchall()
            applied: dict[str, str] = {r[0]: r[1] for r in rows}
            for f in files:
                sql = f.read_text()
                digest = hashlib.sha256(sql.encode()).hexdigest()
                if f.name in applied:
                    if applied[f.name] != digest:
                        print(f"migration {f.name} changed after being applied", file=sys.stderr)
                        return 1
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO public.schema_migrations (version, sha256) VALUES (%s, %s)", (f.name, digest)
                    )
                print(f"applied {f.name}")
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    print(f"migrations up to date ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
