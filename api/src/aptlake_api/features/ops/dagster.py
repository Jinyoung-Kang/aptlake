"""오케스트레이터(Dagster) GraphQL 읽기 전용 클라이언트 — 도커 네트워크 내부 http://dagster:3000/graphql.

고정 질의만 보낸다 (작업 실행·설정 변경 없음). 실행 설정(run config)은 읽지 않는다.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .service import DagsterUnavailable

Q_RUNS = """query($f: RunsFilter, $n: Int){ runsOrError(filter: $f, limit: $n){ __typename
  ... on Runs { results { runId jobName status creationTime startTime endTime tags { key value } } }
  ... on PythonError { message } } }"""
Q_SCHED = """{ repositoriesOrError { ... on RepositoryConnection { nodes {
  schedules { name cronSchedule executionTimezone scheduleState { status }
              futureTicks(limit: 1) { results { timestamp } } }
  sensors { name minIntervalSeconds nextTick { timestamp }
            sensorState { status ticks(limit: 20, dayRange: 7) { status timestamp skipReason runIds error { message } } } }
} } } }"""
Q_LOGS = """query($id: ID!){ logsForRun(runId: $id, limit: 1000){ __typename  # Dagster 최대 1000
  ... on EventConnection { events { __typename
      ... on MessageEvent { timestamp level message stepKey }
      ... on ExecutionStepFailureEvent { error { className message stack causes { className message } } }
      ... on RunFailureEvent { error { className message } } } }
  ... on PythonError { message } } }"""


class DagsterClient:
    """읽기 전용 GraphQL 호출 + 짧은 캐시 (화면 자동 새로고침이 오케스트레이터를 두드리지 않도록)."""

    def __init__(self, url: str, http: httpx.AsyncClient | None = None, ttl_s: float = 5.0):
        self.url, self.ttl = url, ttl_s
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=1.5))
        self._cache: dict[str, tuple[float, Any]] = {}
        self._run_errors: dict[str, list[dict]] = {}  # 끝난 실행의 오류는 바뀌지 않으므로 영구 캐시

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _query(self, query: str, variables: dict | None = None, cache_key: str | None = None) -> dict:
        if cache_key:
            hit = self._cache.get(cache_key)
            if hit and hit[0] > time.monotonic():
                return hit[1]
        try:
            r = await self.http.post(self.url, json={"query": query, "variables": variables or {}})
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise DagsterUnavailable(type(e).__name__) from e
        if body.get("errors"):
            raise DagsterUnavailable("graphql error")
        data = body["data"]
        if cache_key:
            self._cache[cache_key] = (time.monotonic() + self.ttl, data)
        return data

    async def ping(self) -> None:
        """연결 점검용 가장 가벼운 질의 (캐시 없음). 버전 문자열은 밖으로 내보내지 않는다."""
        await self._query("{ version }")

    async def runs(
        self, statuses: list[str] | None = None, created_after: float | None = None, limit: int = 50
    ) -> list[dict]:
        f: dict[str, Any] = {}
        if statuses:
            f["statuses"] = statuses
        if created_after:
            f["createdAfter"] = created_after
        key = f"runs:{statuses}:{int(created_after or 0) // 60}:{limit}"
        data = await self._query(Q_RUNS, {"f": f, "n": limit}, key)
        res = data["runsOrError"]
        if res["__typename"] != "Runs":
            raise DagsterUnavailable("runs error")
        return res["results"]

    async def instigators(self) -> dict:
        data = await self._query(Q_SCHED, cache_key="sched")
        nodes = data["repositoriesOrError"].get("nodes") or []
        return {
            "schedules": [s for n in nodes for s in n["schedules"]],
            "sensors": [s for n in nodes for s in n["sensors"]],
        }

    async def run_errors(self, run_id: str) -> list[dict]:
        if run_id in self._run_errors:
            return self._run_errors[run_id]
        data = await self._query(Q_LOGS, {"id": run_id})
        conn = data["logsForRun"]
        if conn["__typename"] != "EventConnection":
            return []
        out = []
        for e in conn["events"]:
            if e["__typename"] == "ExecutionStepFailureEvent" and e.get("error"):
                out.append({"at": _event_ts(e), "step": e.get("stepKey"), "error": e["error"]})
        if not out:  # 단계 실패가 없고 실행만 실패한 경우 (예: 시작 전 오류, 고아 실행 정리)
            for e in conn["events"]:
                if e["__typename"] == "RunFailureEvent":
                    out.append(
                        {
                            "at": _event_ts(e),
                            "step": None,
                            "error": e.get("error") or {"className": None, "message": e.get("message")},
                        }
                    )
        self._run_errors[run_id] = out
        return out


def _event_ts(e: dict) -> float | None:
    """이벤트 로그의 timestamp 는 밀리초 문자열 (실행 기록의 startTime 등은 초 단위 실수)."""
    ts = e.get("timestamp")
    return float(ts) / 1000 if ts else None
