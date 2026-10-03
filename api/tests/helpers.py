"""테스트 공용 도구: 키 발급, 특정 IP 로 보내는 클라이언트, 가짜 Dagster GraphQL."""

from __future__ import annotations

import httpx


async def new_key(adm, admin_key, plan="free", scopes=("read",)) -> tuple[str, str]:
    r = await adm.post(
        "/v1/admin/clients", json={"name": f"c-{plan}", "planId": plan}, headers={"X-API-Key": admin_key}
    )
    assert r.status_code == 201
    cid = r.json()["clientId"]
    r = await adm.post(f"/v1/admin/clients/{cid}/keys", json={"scopes": list(scopes)}, headers={"X-API-Key": admin_key})
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    return r.json()["apiKey"], cid


def client_at(app, ip: str, key: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 1)),
        base_url="http://t",
        headers={"X-API-Key": key} if key else None,
    )


def fake_dagster(now: float):
    """Dagster GraphQL 가짜 응답: 이후 성공으로 복구된 실패 1건, 아직 실패 중 1건, 대기·실행 중 작업, 오류 난 센서 틱."""
    import json

    from aptlake_api import ops

    def run(rid, status, created, partition="202407", ended=True):
        return {
            "runId": rid,
            "jobName": "month_pipeline",
            "status": status,
            "creationTime": created,
            "startTime": created + 5 if status != "QUEUED" else None,
            "endTime": created + 60 if ended else None,
            "tags": [{"key": "dagster/partition", "value": partition}, {"key": "aptlake/priority", "value": "retry"}],
        }

    failed = [run("fail-old", "FAILURE", now - 7200), run("fail-new", "FAILURE", now - 600, "202408")]
    succeeded = [run("ok-1", "SUCCESS", now - 3600)]
    active = [run("q-1", "QUEUED", now - 30, "202409", False), run("r-1", "STARTED", now - 90, "202410", False)]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        q, v = body["query"], body.get("variables") or {}
        if q.strip() == "{ version }":
            data = {"version": "test"}
        elif "logsForRun" in q:
            data = {
                "logsForRun": {
                    "__typename": "EventConnection",
                    "events": [
                        {
                            "__typename": "ExecutionStepFailureEvent",
                            "timestamp": str(int((now - 550) * 1000)),  # 이벤트 시각은 밀리초
                            "stepKey": "bronze__rtms",
                            "error": {
                                "className": "HTTPError",
                                "message": f"502 for https://apis.data.go.kr/x?serviceKey=LEAKME ({v['id']})",
                                "stack": ["  File a.py\n"],
                                "causes": [],
                            },
                        }
                    ],
                }
            }
        elif "repositoriesOrError" in q:
            sensor = {
                "name": "due_partitions_sensor",
                "minIntervalSeconds": 300,
                "nextTick": {"timestamp": now + 60},
                "sensorState": {
                    "status": "RUNNING",
                    "ticks": [
                        {
                            "status": "FAILURE",
                            "timestamp": now - 100,
                            "skipReason": None,
                            "runIds": [],
                            "error": {"message": "boom postgresql://pipeline:hunter2@postgres/aptlake"},
                        },
                        {  # 앞선 실패는 이 뒤 정상 틱으로 해결됨 (최근 실패는 그대로 미해결)
                            "status": "FAILURE",
                            "timestamp": now - 900,
                            "skipReason": None,
                            "runIds": [],
                            "error": {"message": "old boom"},
                        },
                        {"status": "SKIPPED", "timestamp": now - 600, "skipReason": "x", "runIds": [], "error": None},
                    ],
                },
            }
            data = {"repositoriesOrError": {"nodes": [{"schedules": [], "sensors": [sensor]}]}}
        else:
            st = (v.get("f") or {}).get("statuses")
            rows = {"FAILURE": failed, "SUCCESS": succeeded}.get(st[0] if st and len(st) == 1 else "")
            if rows is None:
                rows = active if st else failed + succeeded
            data = {"runsOrError": {"__typename": "Runs", "results": rows}}
        return httpx.Response(200, json={"data": data})

    return ops.DagsterClient("http://dagster/graphql", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
