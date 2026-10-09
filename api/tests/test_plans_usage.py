"""플랜·사용량·청구 단위 (ADR-047): 공개 플랜은 DB 에서, 오늘 행은 키 기준, 달별 단위는 고객 기준."""

from __future__ import annotations

import asyncio
import datetime as dt

import psycopg
from helpers import client_at, new_key

from aptlake_api.core import keys


async def test_public_plans_come_from_db_without_internal_plans(apps):
    async with client_at(apps[0], "10.83.0.1", None) as c:  # 키 없이도
        r = await c.get("/v1/plans")
    assert r.status_code == 200
    assert r.json()["plans"] == [
        {"planId": "anonymous", "rpm": 20, "dailyRows": 5000, "maxRangeMonths": 12, "maxPageSize": 100, "bulk": False},
        {"planId": "free", "rpm": 60, "dailyRows": 50000, "maxRangeMonths": 24, "maxPageSize": 200, "bulk": False},
        {"planId": "pro", "rpm": 600, "dailyRows": 2000000, "maxRangeMonths": None, "maxPageSize": 1000, "bulk": True},
    ]


async def test_today_rows_are_per_key_and_304_is_not_charged(apps, adm, admin_key):
    key, _ = await new_key(adm, admin_key, "free")
    params = {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 7, "includeCancelled": "true"}
    async with client_at(apps[0], "10.83.0.2", key) as c:
        before = (await c.get("/v1/me/usage")).json()["today"]
        page = await c.get("/v1/trades", params=params)
        n = len(page.json()["items"])
        again = await c.get("/v1/trades", params=params, headers={"If-None-Match": page.headers["etag"]})
        assert again.status_code == 304
        after = (await c.get("/v1/me/usage")).json()
    assert n > 0 and after["today"]["rowsUsed"] == before["rowsUsed"] + n
    assert after["today"]["rowsLeft"] == 50000 - after["today"]["rowsUsed"]
    assert after["limits"]["maxPageSize"] == 200 and after["limits"]["bulk"] is False


async def test_monthly_units_add_up_requests_rows_and_exports(apps, stack, adm, admin_key):
    key, cid = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    with psycopg.connect(stack["su"], autocommit=True) as pg:  # 끝난 내보내기 1건 (1,234행)
        pg.execute(
            """INSERT INTO api.export_job (client_id, key_id, status, params, rows, started_at, finished_at)
               VALUES (%s, %s, 'done', '{}', 1234, now(), now())""",
            (cid, keys.parse(key)[0]),
        )
    params = {"sggCd": "41135", "from": "2024-07-01", "to": "2024-07-31", "limit": 5}
    async with client_at(apps[0], "10.83.0.3", key) as c:
        n = len((await c.get("/v1/trades", params=params)).json()["items"])
        await c.get("/v1/regions")
        await asyncio.sleep(1.5)  # 사용량 기록 일괄 적재
        r = await c.get("/v1/me/usage/monthly", params={"months": 1})
    assert r.status_code == 200
    [m] = r.json()["items"]
    month = dt.datetime.now(tz=dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m")
    assert m["month"] == month and m["requests"] >= 2 and m["chargedRows"] == n
    assert (m["exports"], m["exportRows"]) == (1, 1234) and r.json()["basis"] == "client"
    async with client_at(apps[0], "10.83.0.4", None) as c:
        assert (await c.get("/v1/me/usage/monthly")).status_code == 401
