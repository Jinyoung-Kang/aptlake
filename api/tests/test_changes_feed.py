"""변경분 피드 API 계약 (ADR-045): 스코프·플랜, 버전 창, 커서, 행 차감 — 실제 PostgreSQL·ClickHouse 로."""

from __future__ import annotations

import datetime as dt

import psycopg
from helpers import client_at, new_key

BASE, P1 = "c0000000-0000-0000-0000-000000000001", "c0000000-0000-0000-0000-000000000002"


def seed(stack) -> None:
    now = dt.datetime.now(tz=dt.UTC)
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        for ver, ago, pid, base in (("gold@2026-10-11.1", 2, BASE, True), ("gold@2026-10-11.2", 1, P1, False)):
            pg.execute(
                """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots,
                       row_counts, publish_id, snapshot_baseline)
                   VALUES (%s, %s, %s, ARRAY['202407'], '{}', '{}', %s, %s) ON CONFLICT DO NOTHING""",
                (ver, now - dt.timedelta(hours=ago), now, pid, base),
            )
    ch = stack["ch"]
    ch.command("TRUNCATE TABLE IF EXISTS trade_change")
    cols = ["publish_id", "deal_ym", "trade_id", "change", "changed_fields", "sgg_cd", "deal_date", "complex_key",
            "apt_nm", "umd_nm", "jibun", "area_m2", "floor", "price_manwon", "ppm2", "is_cancelled", "cancel_date",
            "registered_date", "apt_dong", "deal_kind", "seller_type", "buyer_type", "build_year", "is_outlier",
            "version", "valid_from", "missing_since"]  # fmt: skip

    def row(pid, tid, change, fields=()):
        return [pid, 202407, tid, change, list(fields), "41135", dt.date(2024, 7, 5), "c1", "단지", "정자동", "1",
                84.99, 5, 50000, 588.3, 1 if change == "updated" else 0, None, None, "", "중개거래", "개인", "개인",
                2000, 0, 2 if change == "updated" else 1, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), None]  # fmt: skip

    ch.insert("trade_change", [row(P1, "41135-202407-b-0", "updated", ["cancelled"]), row(P1, "41135-202407-a-0", "added"),
                               row("c0000000-0000-0000-0000-00000000ffff", "x", "added")], column_names=cols)  # fmt: skip


async def test_change_feed_contract(apps, stack, adm, admin_key):
    seed(stack)
    pro, _ = await new_key(adm, admin_key, "pro", scopes=("read", "bulk"))
    free, _ = await new_key(adm, admin_key, "free", scopes=("read", "bulk"))
    read_only, _ = await new_key(adm, admin_key, "pro", scopes=("read",))
    async with client_at(apps[0], "10.81.0.1", pro) as c:
        r = await c.get("/v1/trades/changes", params={"since": "gold@2026-10-11.1", "limit": 1})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [(i["tradeId"], i["change"]) for i in body["items"]] == [("41135-202407-a-0", "added")]
        assert body["toVersion"] == "gold@2026-10-11.2" and r.headers["x-ratelimit-limit"]
        nxt = await c.get("/v1/trades/changes",
                          params={"since": "gold@2026-10-11.1", "limit": 1, "cursor": body["page"]["nextCursor"]})  # fmt: skip
        item = nxt.json()["items"][0]
        assert item["tradeId"] == "41135-202407-b-0" and item["changedFields"] == ["cancelled"]
        assert (
            item["trade"]["cancelled"] is True and nxt.json()["page"]["nextCursor"] is None
        )  # 기록 안 된 발행은 안 보임
        bad = await c.get("/v1/trades/changes",
                          params={"since": "gold@2026-10-11.1", "limit": 2, "cursor": body["page"]["nextCursor"]})  # fmt: skip
        assert bad.status_code == 400 and bad.json()["code"] == "INVALID_CURSOR"
        r = await c.get("/v1/trades/changes", params={"since": "gold@2099-01-01.1"})
        assert r.status_code == 404 and r.json()["code"] == "VERSION_NOT_FOUND"
    async with client_at(apps[0], "10.81.0.2", free) as c:
        r = await c.get("/v1/trades/changes", params={"since": "gold@2026-10-11.1"})
        assert r.status_code == 403 and r.json()["code"] == "PLAN_NOT_ALLOWED"
    async with client_at(apps[0], "10.81.0.3", read_only) as c:
        r = await c.get("/v1/trades/changes", params={"since": "gold@2026-10-11.1"})
        assert r.status_code == 403
