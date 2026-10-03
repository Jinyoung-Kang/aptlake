"""수집 상태 · 오류 로그 · 연결 점검 (웹 [수집 상태] 메뉴) — 업무 규칙 (순수).

출처
  - 작업 큐·스케줄: 오케스트레이터(Dagster) 읽기 전용 고정 질의
  - 오류 로그: ① Dagster 실행·단계 실패(오류 클래스·메시지·스택) ② 센서 틱 오류
              ③ 원천 수집 오류(ops.ingest_partition, 같은 달·같은 오류는 묶음) ④ 품질 검사 실패(ops.dq_result)
              ⑤ API 5xx (ClickHouse usage_event — 키·클라이언트 식별자는 내보내지 않음) ⑥ 원천 한도 소진

공개 화면에 나가는 운영 정보이므로 비밀값을 가린다(redact): 인증키 파라미터, DSN 비밀번호, API 키, 토큰.
실행 설정(run config)은 내보내지 않는다. Dagster 가 꺼져 있으면(`make serve`) 해당 부분만 'unavailable'.
운영 메타데이터이므로 일일 '데이터 행' 한도에 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import time
import zlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ...core.redact import redact
from ...core.values import ts

Row = dict[str, Any]
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
DEFAULT_DAILY_LIMIT = 10000  # 오늘 예산 행이 아직 없을 때 (파이프라인이 첫 호출 때 만든다)
CHECK_TIMEOUT_S = 2.0
MAX_ENTRIES = 500


class DagsterUnavailable(Exception):
    """오케스트레이터에 연결할 수 없음 — 해당 부분만 빼고 나머지를 보여 준다."""


class SourceUnavailable(Exception):
    """서빙 DB 일시 장애(시간·메모리 한도 등) — 사유 코드만 담는다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Orchestrator(Protocol):
    async def ping(self) -> None: ...
    async def runs(
        self, statuses: list[str] | None = None, created_after: float | None = None, limit: int = 50
    ) -> list[dict]: ...
    async def instigators(self) -> dict: ...
    async def run_errors(self, run_id: str) -> list[dict]: ...


class OpsStore(Protocol):
    async def budget_day(self, day: dt.date) -> Row | None: ...
    async def partition_counts(self) -> dict[str, int]: ...
    async def month_counts(self) -> Row | None: ...
    async def last_version(self) -> Row | None: ...
    async def log_view(self) -> Row | None: ...
    async def set_errors_cleared(self, clear: bool, actor: str) -> dt.datetime | None: ...
    async def ingest_errors(self, since: dt.datetime) -> list[Row]: ...
    async def budget_exhaustions(self, since: dt.datetime) -> list[Row]: ...
    async def quality_failures(self, since: dt.datetime) -> list[Row]: ...
    async def api_5xx(self, since: dt.datetime) -> list[Row]: ...
    async def api_error_summary(self, since: dt.datetime) -> list[Row]: ...
    async def last_fetched(self) -> dt.datetime | None: ...
    async def boundary_fetched(self) -> dt.datetime | None: ...
    async def ping_pg(self) -> None: ...
    async def cached_dataset_version(self) -> str | None: ...
    async def serving_health(self) -> tuple[dt.date | None, float | None, float | None]: ...


# ───────────────────────── Dagster 응답 해석 ─────────────────────────


def epoch_iso(v: float | int | str | None) -> str | None:
    """초 단위 유닉스 시각 → ISO 8601 (UTC). 범위를 벗어난 값은 버린다 (한 항목 때문에 목록 전체가 실패하지 않게)."""
    if v is None or v == "":
        return None
    try:
        return dt.datetime.fromtimestamp(float(v), tz=dt.UTC).isoformat()
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
        "requestedAt": epoch_iso(r.get("creationTime")),
        "startedAt": epoch_iso(start),
        "endedAt": epoch_iso(end),
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
                "at": epoch_iso(e["at"]) or job["endedAt"] or job["requestedAt"],
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


def stop_label(reason: str | None) -> str:
    """파이프라인이 그날 수집을 멈춘 사유 → 사람이 할 일이 드러나는 문구."""
    if reason and reason.startswith("KeyRejected"):
        return "원천이 인증키를 거부 — .env 의 DATA_GO_KR_KEY(만료·승인) 확인, 자정(KST) 뒤 다시 시도"
    return "원천 일일 한도 초과 — 자정(KST) 뒤 자동 재개 (같은 키를 쓰는 다른 프로그램 호출도 합산)"


def _schedule_row(s: dict) -> dict:
    nxt = (s.get("futureTicks") or {}).get("results") or []
    return {
        "name": s["name"],
        "label": SCHEDULE_LABEL.get(s["name"], s["name"]),
        "type": "schedule",
        "status": s["scheduleState"]["status"],
        "rule": f"cron {s['cronSchedule']} ({s['executionTimezone']})",
        "nextAt": epoch_iso(nxt[0]["timestamp"]) if nxt else None,
        "lastTick": None,
    }


def _sensor_row(s: dict) -> dict:
    ticks = s["sensorState"].get("ticks") or []
    last = ticks[0] if ticks else None
    return {
        "name": s["name"],
        "label": SCHEDULE_LABEL.get(s["name"], s["name"]),
        "type": "sensor",
        "status": s["sensorState"]["status"],
        "rule": f"{s['minIntervalSeconds'] // 60}분마다 평가",
        "nextAt": epoch_iso((s.get("nextTick") or {}).get("timestamp")),
        "lastTick": None
        if not last
        else {
            "status": last["status"],
            "at": epoch_iso(last["timestamp"]),
            "skipReason": redact(last.get("skipReason")),
            "error": redact((last.get("error") or {}).get("message")) or None,
            "runs": len(last.get("runIds") or []),
        },
    }


# ───────────────────────── 수집 상태 ─────────────────────────


async def status(
    dag: Orchestrator, store: OpsStore, *, today: dt.date, budget_pct: int, now: dt.datetime
) -> tuple[dict, int]:
    pipeline: dict[str, Any] = {"available": True}
    active: list[dict] = []
    recent: list[dict] = []
    schedules: list[dict] = []
    try:
        active = [job_row(r) for r in await dag.runs(ACTIVE, limit=100)]
        since = time.time() - 48 * 3600
        recent = [job_row(r) for r in await dag.runs(None, created_after=since, limit=80) if r["status"] not in ACTIVE]
        inst = await dag.instigators()
        schedules = [_schedule_row(s) for s in inst["schedules"]] + [_sensor_row(s) for s in inst["sensors"]]
    except DagsterUnavailable as e:
        pipeline = {
            "available": False,
            "reason": f"오케스트레이터(Dagster)에 연결할 수 없음 — {e}. 수집 스택이 꺼져 있으면(make serve) 정상입니다.",
        }

    b = await store.budget_day(today)
    counts = await store.partition_counts()
    months = await store.month_counts()
    ver = await store.last_version()
    daily_limit = int(b["limit_calls"]) if b else DEFAULT_DAILY_LIMIT
    cap = daily_limit * budget_pct // 100
    remaining_calls = counts.get("PENDING", 0) + counts.get("RETRY", 0)
    return {
        "pipeline": pipeline,
        "summary": {
            "partitions": counts,
            "totalPartitions": sum(counts.values()),
            "publishedMonths": months["published"] if months else 0,
            "publishPending": months["pending"] if months else 0,
            "budget": {
                "day": today.isoformat(),
                "limit": daily_limit,
                "cap": cap,
                "used": int(b["used_calls"]) if b else 0,
                "byPriority": b["by_priority"] if b else {},
                "exhaustedReason": b["exhausted_reason"] if b else None,
                "stopLabel": stop_label(b["exhausted_reason"]) if b and b["exhausted_reason"] else None,
                "exhaustedAt": ts(b["exhausted_at"]) if b else None,
            },
            # 추정: 남은 파티션(시군구×월) 1개 = 원천 호출 1회, 하루 상한만큼 쓴다고 가정한 단순 계산
            "backfillEstimate": {
                "remainingCalls": remaining_calls,
                "days": math.ceil(remaining_calls / cap) if cap else None,
                "basis": "남은 파티션 수 ÷ 일일 호출 상한 (다른 프로그램과 키를 공유하면 더 걸림)",
            },
            "lastPublish": {"version": ver["version"], "at": ts(ver["published_at"])} if ver else None,
        },
        "jobs": {"active": active, "recent": recent},
        "schedules": schedules,
        "serverTime": now.isoformat(),
    }, 0


# ───────────────────────── 오류 로그 ─────────────────────────


async def _pipeline_entries(dag: Orchestrator, since: dt.datetime, include_resolved: bool) -> list[dict]:
    entries: list[dict] = []
    failed = await dag.runs(["FAILURE"], created_after=since.timestamp(), limit=40)
    # 같은 작업·파티션이 나중에 성공했으면 '해결됨' (재시도·재실행으로 복구된 실패)
    last_ok: dict[tuple[str, str | None], float] = {}
    for r in await dag.runs(["SUCCESS"], created_after=since.timestamp(), limit=500):
        k = (r["jobName"], job_row(r)["partition"])
        last_ok[k] = max(last_ok.get(k, 0.0), r.get("creationTime") or 0.0)
    sem = asyncio.Semaphore(5)

    async def one(r: dict) -> list[dict]:
        job = job_row(r)
        resolved = last_ok.get((r["jobName"], job["partition"]), 0.0) > (r.get("creationTime") or 0.0)
        if resolved and not include_resolved:
            return []  # 숨길 항목은 로그도 읽지 않는다
        async with sem:
            try:
                errs = await dag.run_errors(r["runId"])
            except DagsterUnavailable:
                # 한 실행의 로그를 못 읽어도 나머지는 보여 준다 (실패 사실은 실행 기록만으로도 알 수 있다)
                errs = [
                    {"at": None, "step": None, "error": {"message": "실행 로그를 읽지 못했습니다 (Dagster 응답 없음)"}}
                ]
        out = run_error_entries(job, errs)
        for e in out:
            e["resolved"] = resolved
        return out

    for part in await asyncio.gather(*(one(r) for r in failed)):
        entries += part
    for s in (await dag.instigators())["sensors"]:
        ticks = s["sensorState"].get("ticks") or []
        # 실행 실패와 같은 규칙: 그 뒤에 오류 없이 끝난 틱(요청·건너뜀)이 있으면 '해결됨'
        tick_ok = max((float(t["timestamp"]) for t in ticks if not t.get("error")), default=0.0)
        for t in ticks:
            if t.get("error") and float(t["timestamp"]) >= since.timestamp():
                tick_resolved = tick_ok > float(t["timestamp"])
                if tick_resolved and not include_resolved:
                    continue
                entries.append(
                    {
                        "id": f"tick:{s['name']}:{t['timestamp']}",
                        "at": epoch_iso(t["timestamp"]),
                        "level": "ERROR",
                        "source": "pipeline",
                        "where": SCHEDULE_LABEL.get(s["name"], s["name"]),
                        "message": redact(t["error"]["message"]).splitlines()[0][:300],
                        "detail": redact(t["error"]["message"]),
                        "ref": None,
                        "resolved": tick_resolved,
                    }
                )
    return entries


def _ingest_entry(r: Row) -> dict:
    label = "격리" if r["status"] == "QUARANTINED" else "재시도 대기"
    ym = f"{r['deal_ym'][:4]}-{r['deal_ym'][4:]}"
    return {
        "id": f"ingest:{r['deal_ym']}:{r['status']}:{zlib.crc32(r['last_error'].encode()):08x}",
        "at": ts(r["at"]),
        "level": "ERROR" if r["status"] == "QUARANTINED" else "WARN",
        "source": "ingest",
        "where": f"원천 수집 · {ym} · 시군구 {r['n']}개 {label}",
        "message": redact(r["last_error"]).splitlines()[0][:300],
        "detail": redact(r["last_error"])
        + f"\n\n대상 시군구(최대 8개): {', '.join(r['sample'])}"
        + (f" 외 {r['n'] - len(r['sample'])}개" if r["n"] > len(r["sample"]) else ""),
        "ref": None,
        "resolved": False,
    }


def _budget_entry(r: Row) -> dict:
    return {
        "id": f"budget:{r['day']}",
        "at": ts(r["exhausted_at"]),
        "level": "WARN",
        "source": "ingest",
        "where": f"원천 호출 예산 · {r['day']}",
        "message": f"{stop_label(r['exhausted_reason'])} ({redact(r['exhausted_reason'])})",
        "detail": "공공데이터포털 한도는 인증키 단위라 같은 키를 쓰는 다른 프로그램의 호출도 합산됩니다.",
        "ref": None,
        "resolved": False,
    }


def _quality_entry(r: Row) -> dict:
    return {
        "id": f"dq:{r['check_id']}",
        "at": ts(r["at"]),
        "level": "ERROR" if r["blocking"] else "WARN",
        "source": "quality",
        "where": f"{r['asset']} · {r['partition'] or '-'}",
        "message": f"품질 검사 실패: {r['check_name']}" + (" (차단 — 하위 반영 멈춤)" if r["blocking"] else ""),
        "detail": redact(json.dumps(r["metric"], ensure_ascii=False, indent=2)),
        "ref": None,
        "resolved": bool(r["resolved"]),
    }


def _api_entry(r: Row) -> dict:
    return {
        "id": f"api:{r['trace_id']}",
        "at": ts(r["at"]),
        "level": "ERROR",
        "source": "api",
        "where": f"API {r['route']}",
        "message": f"HTTP {r['status']} · {r['latency_ms']} ms" + (f" · {r['error']}" if r["error"] else ""),
        "detail": f"traceId={r['trace_id']} (서버 로그와 대조: docker compose logs api | grep {r['trace_id']})",
        "ref": r["trace_id"],
        "resolved": False,
    }


def _parse_ts(iso: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(iso)
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


async def errors(
    dag: Orchestrator,
    store: OpsStore,
    *,
    hours: int,
    source: str,
    include_resolved: bool,
    include_cleared: bool,
    now: dt.datetime,
) -> tuple[dict, int]:
    entries: list[dict] = []
    notes: list[str] = []
    since = now - dt.timedelta(hours=hours)
    view = await store.log_view()
    cleared_at: dt.datetime | None = view["cleared_at"] if view else None
    # 비운 뒤라면 API 오류 요약도 그 뒤부터만 센다
    api_since = max(since, cleared_at) if cleared_at and not include_cleared else since

    def want(s: str) -> bool:
        return source in ("all", s)

    if want("pipeline"):
        try:
            entries += await _pipeline_entries(dag, since, include_resolved)
        except DagsterUnavailable:
            notes.append("오케스트레이터(Dagster)에 연결할 수 없어 파이프라인 실행 오류는 빠졌습니다.")

    if want("ingest"):
        entries += [_ingest_entry(r) for r in await store.ingest_errors(since)]
        entries += [_budget_entry(r) for r in await store.budget_exhaustions(since)]

    if want("quality"):
        entries += [
            _quality_entry(r) for r in await store.quality_failures(since) if include_resolved or not r["resolved"]
        ]

    api_summary: list[dict] = []
    if want("api"):
        # 서빙 DB 가 잠깐 느리면(예: 도커 VM 메모리 부족) 이 부분만 빼고 나머지 로그는 보여 준다 — Dagster 와 같은 규칙.
        # 예전에는 이 질의 하나의 시간 초과로 오류 로그 화면 전체가 503 이었다
        try:
            entries += [_api_entry(r) for r in await store.api_5xx(since)]
            api_summary = [
                {"route": r["route"], "status": r["status"], "count": r["n"]}
                for r in await store.api_error_summary(api_since)
            ]
        except SourceUnavailable as down:
            api_summary = []
            notes.append(
                f"서빙 DB(ClickHouse)가 응답하지 않아({down.reason}) API 오류 항목은 빠졌습니다. 잠시 후 새로고침하세요."
            )

    # 비우기 기준 시각 이전 항목: 기본은 숨김, includeCleared 면 cleared=true 로 표시
    if cleared_at:
        mark = cleared_at.isoformat()
        for e in entries:
            e["cleared"] = bool(e["at"]) and _parse_ts(e["at"]) <= cleared_at
        hidden = sum(e["cleared"] for e in entries)
        if not include_cleared:
            entries = [e for e in entries if not e["cleared"]]
        notes.append(f"비우기 이전 항목 {hidden}건은 숨김 (기준 {mark})" if not include_cleared and hidden else "")
        notes = [n for n in notes if n]
    else:
        for e in entries:
            e["cleared"] = False
    entries.sort(key=lambda e: e["at"] or "", reverse=True)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e["source"]] = counts.get(e["source"], 0) + 1
    return {
        "window": {"hours": hours, "since": since.isoformat()},
        "counts": counts,
        "entries": entries[:MAX_ENTRIES],
        "truncated": len(entries) > MAX_ENTRIES,
        "apiErrorSummary": api_summary,
        "notes": notes,
        "cleared": {"at": ts(cleared_at), "by": view["cleared_by"]} if cleared_at and view else None,
    }, 0


async def set_cleared(store: OpsStore, clear: bool, actor: str) -> dict:
    """'비우기'는 기준 시각만 남긴다 (원본 기록은 지우지 않음). 기준 변경과 감사 기록은 한 트랜잭션."""
    at = await store.set_errors_cleared(clear, actor[:40])
    return {"cleared": {"at": ts(at)} if at else None}


# ───────────────────────── 연결 점검 ─────────────────────────


async def _timed(fn: Callable[[], Awaitable[str | None]]) -> dict[str, Any]:
    """점검 하나: 성공 여부·지연(ms)·비고. 오류는 종류만 (내부 주소·메시지는 내보내지 않음)."""
    t0 = time.perf_counter()
    try:
        note = await asyncio.wait_for(fn(), CHECK_TIMEOUT_S)
        return {"ok": True, "latencyMs": round((time.perf_counter() - t0) * 1000, 1), "note": note, "error": None}
    except TimeoutError:
        return {"ok": False, "latencyMs": None, "note": None, "error": f"{CHECK_TIMEOUT_S:.0f}초 안에 응답 없음"}
    except Exception as e:  # noqa: BLE001 — 점검 결과로 돌려준다
        return {
            "ok": False,
            "latencyMs": round((time.perf_counter() - t0) * 1000, 1),
            "note": None,
            "error": type(e).__name__,
        }


CHECKS = [
    ("postgres", "운영 DB · PostgreSQL", "API 키·플랜·수집 상태"),
    ("redis", "캐시 · Redis", "요청 한도·결과 캐시·키 캐시"),
    ("clickhouse", "서빙 DB · ClickHouse", "거래·통계 조회"),
    ("dagster", "오케스트레이터 · Dagster", "수집 작업 큐 (수집 상태 화면)"),
]


async def _sources(store: OpsStore, today: dt.date, budget_pct: int) -> list[dict[str, Any]]:
    fetched = await store.last_fetched()
    b = await store.budget_day(today)
    boundary = await store.boundary_fetched()
    cap = b["limit_calls"] * budget_pct // 100 if b else None
    if b and b["exhausted_reason"]:
        rtms_state = stop_label(b["exhausted_reason"])
    elif b and cap is not None and b["used_calls"] >= cap:
        rtms_state = f"오늘 호출 상한({budget_pct}%) 도달 — 자정(KST) 뒤 재개"
    else:
        rtms_state = "수집 가능"
    return [
        {
            "key": "rtms",
            "name": "국토교통부 아파트 매매 실거래가",
            "lastSuccessAt": ts(fetched),
            "state": rtms_state,
            "detail": f"오늘 호출 {b['used_calls']:,} / 상한 {cap:,} (일 한도 {b['limit_calls']:,})"
            if b
            else "오늘 호출 기록 없음",
        },
        {
            "key": "vworld",
            "name": "국토정보플랫폼 V-World 시군구 경계",
            "lastSuccessAt": ts(boundary),
            "state": "매월 갱신",
            "detail": None,
        },
    ]


async def connectivity(
    dag: Orchestrator, store: OpsStore, *, today: dt.date, budget_pct: int, now: Callable[[], dt.datetime]
) -> tuple[dict, int]:
    """구성요소마다 가장 가벼운 질의 한 번 (동시 실행, 각 2초 제한).

    외부 원천(국토부·V-World)은 **직접 호출하지 않는다** — 일일 호출 한도가 있는 키를 공개 화면의 버튼으로
    소모하게 만들 수 없으므로, 파이프라인이 기록한 마지막 성공 수집 시각·오늘 예산 상태를 보여 준다.
    """

    async def pg() -> None:
        await store.ping_pg()

    async def rd() -> str:
        ver = await store.cached_dataset_version()
        return f"발행 버전 {ver}" if ver else "발행 버전 없음"

    async def ch() -> str:
        m, used, cap = await store.serving_health()
        month = f"최근 계약월 {m:%Y-%m}" if m else "데이터 없음"
        # 서버 메모리 상한에 가까우면 질의가 거부된다 (2026-09-26 장애 원인) → 점검 화면에 같이 보인다
        mem = f" · 메모리 {used / 2**20:,.0f}MB / 상한 {cap / 2**20:,.0f}MB" if used is not None and cap else ""
        return month + mem

    async def dg() -> None:
        await dag.ping()

    results = await asyncio.gather(*(_timed(f) for f in (pg, rd, ch, dg)))
    items = [{"key": k, "name": n, "role": r, **res} for (k, n, r), res in zip(CHECKS, results, strict=True)]

    # 원천 API: 직접 호출하지 않고 마지막 성공 수집·오늘 예산으로 판단
    try:
        sources = await _sources(store, today, budget_pct)
    except Exception as e:  # noqa: BLE001 — 운영 DB 장애는 위 점검 항목에 이미 나타난다
        sources = [
            {
                "key": "error",
                "name": "원천 상태를 읽지 못함",
                "lastSuccessAt": None,
                "state": type(e).__name__,
                "detail": None,
            }
        ]

    return {
        "checkedAt": now().isoformat(),
        "ok": all(i["ok"] for i in items if i["key"] != "dagster"),  # Dagster 는 수집 스택이 꺼져 있어도 서빙은 정상
        "items": items,
        "sources": sources,
        "notes": [
            "외부 원천 API 는 일일 호출 한도 보호를 위해 이 화면에서 직접 호출하지 않습니다 (마지막 성공 수집 시각으로 판단).",
            "Dagster 는 수집 스택(make up)일 때만 켜져 있습니다. 꺼져 있어도 조회 API 는 정상 동작합니다.",
        ],
    }, 0
