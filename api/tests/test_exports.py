"""대량 내려받기(Parquet 내보내기) API 계약: 스코프·플랜·기간 검사, 클라이언트 격리, 동시 2개 한도."""

from __future__ import annotations

import psycopg
from helpers import client_at, new_key

from aptlake_api import keys

BODY = {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31"}


async def test_export_requires_bulk_scope_and_pro_plan(apps, adm, admin_key):
    free_bulk, _ = await new_key(adm, admin_key, "free", scopes=("read", "bulk"))
    async with client_at(apps[0], "10.80.0.1", free_bulk) as c:
        r = await c.post("/v1/exports", json=BODY)
    assert r.status_code == 403 and r.json()["code"] == "PLAN_NOT_ALLOWED"


async def test_export_create_get_and_isolation(apps, adm, admin_key):
    mine, _ = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    other, _ = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    async with client_at(apps[0], "10.80.0.2", mine) as c, client_at(apps[0], "10.80.0.3", other) as o:
        bad = await c.post("/v1/exports", json={**BODY, "from": "2024-08-01"})
        assert bad.status_code == 400 and bad.json()["code"] == "INVALID_RANGE"

        r = await c.post("/v1/exports", json=BODY)
        assert r.status_code == 202
        job = r.json()
        assert job["status"] == "queued" and r.headers["location"] == f"/v1/exports/{job['jobId']}"

        got = await c.get(f"/v1/exports/{job['jobId']}")
        assert got.status_code == 200 and got.json()["jobId"] == job["jobId"]
        # 작업자가 이미 집어 갔을 수 있다 (테스트 환경엔 S3 가 없어 곧 실패로 끝남)
        assert got.json()["status"] in {"queued", "running", "failed"}

        # 다른 클라이언트의 작업은 존재 여부도 알려 주지 않는다
        assert (await o.get(f"/v1/exports/{job['jobId']}")).status_code == 404


async def test_export_concurrency_limit_per_client(apps, stack, adm, admin_key):
    key, cid = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    kid = keys.parse(key)[0]
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        for _ in range(2):  # 진행 중 2개 (작업자는 running 을 건드리지 않는다)
            pg.execute(
                """INSERT INTO api.export_job (client_id, key_id, status, params, started_at)
                   VALUES (%s, %s, 'running', '{}', now())""",
                (cid, kid),
            )
    async with client_at(apps[0], "10.80.0.4", key) as c:
        r = await c.post("/v1/exports", json=BODY)
    assert r.status_code == 429 and r.json()["code"] == "TOO_MANY_EXPORTS"
