"""관리 API: 변경은 모두 감사 로그에 남는다."""

from __future__ import annotations

import psycopg


def _actions(su: str, target: str) -> list[str]:
    with psycopg.connect(su, autocommit=True) as pg:
        return [r[0] for r in pg.execute("SELECT action FROM api.audit_log WHERE target = %s ORDER BY at", (target,))]


async def test_retry_partition_only_from_quarantine_and_audited(adm, admin_key, stack):
    h = {"X-API-Key": admin_key}
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        pg.execute(
            """INSERT INTO ops.ingest_partition (sgg_cd, deal_ym, status, attempts, last_error)
               VALUES ('41135','203001','QUARANTINED',3,'boom'), ('41135','203002','MERGED',0,NULL)
               ON CONFLICT DO NOTHING"""
        )
    r = await adm.post("/v1/admin/partitions/41135/2030-02/retry", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "PARTITION_NOT_QUARANTINED"

    r = await adm.post("/v1/admin/partitions/41135/2030-01/retry", headers=h)
    assert r.status_code == 202 and r.json()["status"] == "RETRY"
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        row = pg.execute(
            "SELECT status, attempts, last_error FROM ops.ingest_partition WHERE sgg_cd='41135' AND deal_ym='203001'"
        ).fetchone()
    assert row == ("RETRY", 0, None)
    assert _actions(stack["su"], "41135/203001") == ["partition.retry"]
