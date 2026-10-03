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


async def test_admin_change_is_rolled_back_when_audit_fails(apps, adm, admin_key, stack, monkeypatch):
    """감사 기록이 실패하면 변경도 남지 않아야 한다 (기록 없는 키 발급·폐기 금지)."""
    import httpx

    from aptlake_api import routes_admin

    h = {"X-API-Key": admin_key}
    # 처리되지 않은 예외도 테스트에서 500 응답으로 받는다 (기본은 예외를 그대로 올림)
    raw = httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1], raise_app_exceptions=False), base_url="http://t")
    cid = (await adm.post("/v1/admin/clients", json={"name": "audit-fail"}, headers=h)).json()["clientId"]
    key = (await adm.post(f"/v1/admin/clients/{cid}/keys", json={"scopes": ["read"]}, headers=h)).json()["keyId"]

    async def broken_audit(*args, **kwargs):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(routes_admin, "audit", broken_audit)
    async with raw:
        r = await raw.post(f"/v1/admin/clients/{cid}/keys", json={"scopes": ["read"]}, headers=h)
        assert r.status_code == 500
        r = await raw.delete(f"/v1/admin/keys/{key}", headers=h)
        assert r.status_code == 500
    with psycopg.connect(stack["su"], autocommit=True) as pg:
        keys = pg.execute("SELECT key_id, revoked_at FROM api.api_key WHERE client_id = %s", (cid,)).fetchall()
    assert keys == [(key, None)]  # 두 번째 키는 만들어지지 않았고, 첫 키는 폐기되지 않았다
