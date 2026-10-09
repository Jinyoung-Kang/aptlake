"""원천 반영 지연 API (ADR-046) — 실제 ClickHouse(source_latency)·PostgreSQL(publish_lag) 로."""

from __future__ import annotations

import datetime as dt

import psycopg
from helpers import client_at, new_key

PID = "d0000000-0000-0000-0000-0000000000a1"


async def test_latency_endpoint_reads_both_stores(apps, stack, adm, admin_key):
    ch = stack["ch"]
    ch.command("TRUNCATE TABLE IF EXISTS source_latency")
    ch.insert(
        "source_latency",
        [["L1", dt.date(2026, 10, 1), "11", "", 3, 5], ["L1", dt.date(2026, 10, 1), "41", "", 20, 5],
         ["L1", dt.date(2026, 9, 26), "11", "initial_load", None, 99], ["L2", dt.date(2026, 10, 2), "11", "", 4, 2]],
        column_names=["metric", "observed_date", "sido_cd", "reason", "lag_days", "n"],
    )  # fmt: skip
    pub = dt.datetime(2026, 10, 2, 3, 0, tzinfo=dt.UTC)
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        pg.execute(
            """INSERT INTO ops.dataset_version (version, published_at, data_as_of, partitions, snapshots, row_counts,
                   publish_id) VALUES ('gold@2026-10-02.9', %s, %s, ARRAY['202609'], '{}', '{}', %s) ON CONFLICT DO NOTHING""",
            (pub, pub, PID),
        )
        pg.execute(
            """INSERT INTO ops.publish_lag (publish_id, sgg_cd, deal_ym, fetched_at, published_at) VALUES
               (%(p)s, '11110', '202609', %(t)s - interval '10 minutes', %(t)s),
               (%(p)s, '41135', '202609', %(t)s - interval '50 minutes', %(t)s) ON CONFLICT DO NOTHING""",
            {"p": PID, "t": pub},
        )
    key, _ = await new_key(adm, admin_key, "free")
    async with client_at(apps[0], "10.82.0.1", key) as c:
        r = await c.get("/v1/quality/latency", params={"from": "2026-09-01", "to": "2026-10-10"})
        assert r.status_code == 200, r.text
        m = r.json()["metrics"]
        assert (m["L1"]["n"], m["L1"]["p50"], m["L1"]["p90"], m["L1"]["excluded"]) == (10, 3, 20, {"initial_load": 99})
        assert m["L1"]["measuredSince"] == "2026-10-01"  # 제외되지 않은 첫 관측일
        assert (m["L3"]["n"], m["L3"]["p50"], m["L3"]["p90"]) == (2, 10, 50)
        assert [b for b in m["L3"]["distribution"] if b[1]] == [[15, 1], [60, 1]]
        seoul = (
            await c.get("/v1/quality/latency", params={"from": "2026-09-01", "to": "2026-10-10", "sido": "11"})
        ).json()
        assert seoul["metrics"]["L1"]["n"] == 5 and seoul["metrics"]["L3"]["n"] == 1
        bad = await c.get("/v1/quality/latency", params={"from": "2026-10-10", "to": "2026-09-01"})
        assert bad.status_code == 400
