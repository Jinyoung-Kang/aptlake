"""수집 상태 · 오류 로그 (웹 [수집 상태] 메뉴).

출처
  - 작업 큐·스케줄: Dagster GraphQL (읽기 전용 고정 질의, 도커 네트워크 내부 http://dagster:3000/graphql)
  - 오류 로그: ① Dagster 실행·단계 실패(오류 클래스·메시지·스택) ② 센서 틱 오류
              ③ 원천 수집 오류(ops.ingest_partition, 같은 달·같은 오류는 묶음) ④ 품질 검사 실패(ops.dq_result)
              ⑤ API 5xx (ClickHouse usage_event — 키·클라이언트 식별자는 내보내지 않음) ⑥ 원천 한도 소진

공개 화면에 나가는 운영 정보이므로 비밀값을 가린다(redact): 인증키 파라미터, DSN 비밀번호, API 키, 토큰.
실행 설정(run config)은 내보내지 않는다. Dagster 가 꺼져 있으면(`make serve`) 해당 부분만 'unavailable'.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

import httpx

# 가릴 이름: 이름 끝이 이 단어면 값을 가린다 (s3.secret-access-key, aws_session_token, X-Amz-Signature, serviceKey …).
# 'key' 단독 이름(V-World ?key=)은 앞에 다른 단어가 없을 때만 — complex_key·stepKey 같은 식별자는 남긴다.
_SENSITIVE_NAME = (
    r"(?:(?<![\w.-])key|(?<![\w.-])[\w.-]*?(?:service_?key|api[-_]?key|crtfc_key|access[-_]?key(?:[-_]?id)?"
    r"|secret(?:[-_]?access)?(?:[-_]?key)?|private[-_]?key|password|passwd|pwd|token|credentials?|signature))"
)
REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"al_live_[2-9A-HJ-NP-Z]{12}\.[A-Za-z0-9_-]{20,}"), "al_live_***"),
    # URL 사용자 정보의 비밀번호 (postgresql+psycopg://user:pw@, redis://:pw@ …)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*)://([^:/@\s]*):([^@\s]+)@"), r"\1://\2:***@"),
    # 인증 헤더 (Bearer/Basic 스킴 뒤의 값까지)
    (
        re.compile(r"(?i)\b(authorization|x-api-key)([\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic|token)\s+)?[^\s\"',}]+"),
        r"\1\2***",
    ),
    # name=value · "name": "value" · 'name': 'value'
    (re.compile(rf"(?i)({_SENSITIVE_NAME})([\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)[^\s\"',}}&]+"), r"\1\2***"),
]


def redact(text: str | None) -> str:
    if not text:
        return ""
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text


JOB_LABEL = {
    "month_pipeline": "월 수집·반영·발행",
    "dims_and_index": "단지 차원·자체 지수",
    "refresh_regions": "시군구·경계 갱신",
    "iceberg_maintenance": "레이크 유지보수",
    "__ASSET_JOB": "자산 실행",
}
PRIORITY_LABEL = {
    "incremental": "증분",
    "recheck": "재확인",
    "backfill": "백필",
    "retry": "재시도",
    "publish-only": "발행 전용",
    "publish-only-manual": "발행 전용(수동)",
}
SCHEDULE_LABEL = {
    "daily_dims_and_index": "매일 단지 차원·지수",
    "weekly_iceberg_maintenance": "매주 레이크 유지보수",
    "monthly_regions": "매월 시군구·경계 갱신",
    "due_partitions_sensor": "수집 센서 (기한 도래 파티션)",
}
ACTIVE = ["QUEUED", "NOT_STARTED", "STARTING", "STARTED", "CANCELING"]

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


class DagsterUnavailable(Exception):
    pass


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


def iso(ts: float | int | str | None) -> str | None:
    """초 단위 유닉스 시각 → ISO 8601 (UTC). 범위를 벗어난 값은 버린다 (한 항목 때문에 목록 전체가 실패하지 않게)."""
    if ts is None or ts == "":
        return None
    try:
        return dt.datetime.fromtimestamp(float(ts), tz=dt.UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def job_row(r: dict) -> dict:
    tags = {t["key"]: t["value"] for t in r.get("tags", [])}
    prio = tags.get("aptlake/priority")
    start, end = r.get("startTime"), r.get("endTime")
    return {
        "runId": r["runId"],
        "job": r["jobName"],
        "jobLabel": JOB_LABEL.get(r["jobName"], r["jobName"]),
        "partition": tags.get("dagster/partition"),
        "priority": prio,
        "priorityLabel": PRIORITY_LABEL.get(prio or "", prio or "수동"),
        "trigger": "센서"
        if tags.get("dagster/sensor_name")
        else "스케줄"
        if tags.get("dagster/schedule_name")
        else "수동",
        "status": r["status"],
        "requestedAt": iso(r.get("creationTime")),
        "startedAt": iso(start),
        "endedAt": iso(end),
        "durationS": round(end - start, 1) if start and end else None,
    }


def stack_tail(stack: list[str] | None, frames: int = 12) -> str:
    """스택은 마지막 N개 프레임만 (오류 지점에 가까운 쪽), 비밀값 가림."""
    if not stack:
        return ""
    return redact("".join(stack[-frames:])).rstrip()


def run_error_entries(job: dict, errors: list[dict]) -> list[dict]:
    out = []
    for i, e in enumerate(errors):
        err = e["error"] or {}
        causes = err.get("causes") or []
        detail = stack_tail(err.get("stack"))
        for c in causes:
            detail += f"\n\n원인: {c.get('className') or ''}: {redact(c.get('message'))}".rstrip()
        msg = redact((err.get("message") or "").strip())
        out.append(
            {
                "id": f"run:{job['runId']}:{i}",
                "at": iso(e["at"]) or job["endedAt"] or job["requestedAt"],
                "level": "ERROR",
                "source": "pipeline",
                "where": " · ".join(x for x in [job["jobLabel"], job["partition"], e.get("step")] if x),
                "message": msg.splitlines()[0][:300] if msg else (err.get("className") or "실패"),
                "detail": (msg + ("\n\n" + detail if detail else "")).strip(),
                "ref": job["runId"],
                "resolved": False,
            }
        )
    return out
