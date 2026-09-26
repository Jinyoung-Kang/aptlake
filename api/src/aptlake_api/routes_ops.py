"""수집 상태 · 오류 로그 API (/v1/ops). 운영 메타데이터이므로 일일 '데이터 행' 한도에 넣지 않는다."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import time
import zlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from . import ops
from .auth import Principal
from .deps import require_scope, respond
from .settings import settings

router = APIRouter(prefix="/v1/ops")
KST = dt.timezone(dt.timedelta(hours=9))


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return (v if v.tzinfo else v.replace(tzinfo=dt.UTC)).isoformat()
    return str(v)


async def _pg(request: Request, sql: str, params: tuple = ()) -> list[dict]:
    async with request.app.state.res.pg.connection() as c:
        return await (await c.execute(sql, params)).fetchall()  # type: ignore[return-value]


@router.get("/status", summary="수집 상태: 요약 · 작업 큐 · 스케줄/센서")
async def status(request: Request, p: Principal = require_scope("read")) -> Response:
    async def compute():
        dag: ops.DagsterClient = request.app.state.dagster
        pipeline: dict[str, Any] = {"available": True}
        active: list[dict] = []
        recent: list[dict] = []
        schedules: list[dict] = []
        try:
            active = [ops.job_row(r) for r in await dag.runs(ops.ACTIVE, limit=100)]
            since = time.time() - 48 * 3600
            recent = [
                ops.job_row(r)
                for r in await dag.runs(None, created_after=since, limit=80)
                if r["status"] not in ops.ACTIVE
            ]
            inst = await dag.instigators()
            for s in inst["schedules"]:
                nxt = (s.get("futureTicks") or {}).get("results") or []
                schedules.append(
                    {
                        "name": s["name"],
                        "label": ops.SCHEDULE_LABEL.get(s["name"], s["name"]),
                        "type": "schedule",
                        "status": s["scheduleState"]["status"],
                        "rule": f"cron {s['cronSchedule']} ({s['executionTimezone']})",
                        "nextAt": ops.iso(nxt[0]["timestamp"]) if nxt else None,
                        "lastTick": None,
                    }
                )
            for s in inst["sensors"]:
                ticks = s["sensorState"].get("ticks") or []
                last = ticks[0] if ticks else None
                schedules.append(
                    {
                        "name": s["name"],
                        "label": ops.SCHEDULE_LABEL.get(s["name"], s["name"]),
                        "type": "sensor",
                        "status": s["sensorState"]["status"],
                        "rule": f"{s['minIntervalSeconds'] // 60}분마다 평가",
                        "nextAt": ops.iso((s.get("nextTick") or {}).get("timestamp")),
                        "lastTick": None
                        if not last
                        else {
                            "status": last["status"],
                            "at": ops.iso(last["timestamp"]),
                            "skipReason": ops.redact(last.get("skipReason")),
                            "error": ops.redact((last.get("error") or {}).get("message")) or None,
                            "runs": len(last.get("runIds") or []),
                        },
                    }
                )
        except ops.DagsterUnavailable as e:
            pipeline = {
                "available": False,
                "reason": f"오케스트레이터(Dagster)에 연결할 수 없음 — {e}. "
                "수집 스택이 꺼져 있으면(make serve) 정상입니다.",
            }

        today = dt.datetime.now(tz=KST).date()
        budget = await _pg(
            request,
            """SELECT day, limit_calls, used_calls, by_priority, exhausted_reason, exhausted_at
                                       FROM ops.api_budget WHERE source='rtms' AND day=%s""",
            (today,),
        )
        parts = await _pg(request, "SELECT status, count(*) AS n FROM ops.ingest_partition GROUP BY status")
        counts = {r["status"]: r["n"] for r in parts}
        months = await _pg(
            request,
            """SELECT count(*) FILTER (WHERE needs_publish) AS pending,
                                              count(*) FILTER (WHERE published_at IS NOT NULL) AS published
                                       FROM ops.month_state""",
        )
        ver = await _pg(
            request,
            """SELECT version, published_at FROM ops.dataset_version
                                    ORDER BY published_at DESC LIMIT 1""",
        )
        daily_limit = int(budget[0]["limit_calls"]) if budget else 10000
        cap = daily_limit * 80 // 100
        remaining_calls = counts.get("PENDING", 0) + counts.get("RETRY", 0)
        b = budget[0] if budget else None
        return {
            "pipeline": pipeline,
            "summary": {
                "partitions": counts,
                "totalPartitions": sum(counts.values()),
                "publishedMonths": months[0]["published"] if months else 0,
                "publishPending": months[0]["pending"] if months else 0,
                "budget": {
                    "day": today.isoformat(),
                    "limit": daily_limit,
                    "cap": cap,
                    "used": int(b["used_calls"]) if b else 0,
                    "byPriority": b["by_priority"] if b else {},
                    "exhaustedReason": b["exhausted_reason"] if b else None,
                    "exhaustedAt": _iso(b["exhausted_at"]) if b else None,
                },
                # 추정: 남은 파티션(시군구×월) 1개 = 원천 호출 1회, 하루 상한만큼 쓴다고 가정한 단순 계산
                "backfillEstimate": {
                    "remainingCalls": remaining_calls,
                    "days": math.ceil(remaining_calls / cap) if cap else None,
                    "basis": "남은 파티션 수 ÷ 일일 호출 상한 (다른 프로그램과 키를 공유하면 더 걸림)",
                },
                "lastPublish": {"version": ver[0]["version"], "at": _iso(ver[0]["published_at"])} if ver else None,
            },
            "jobs": {"active": active, "recent": recent},
            "schedules": schedules,
            "serverTime": dt.datetime.now(tz=dt.UTC).isoformat(),
        }, 0

    return await respond(request, "ops_status", {}, compute, cache=False)


SOURCE = Literal["all", "pipeline", "ingest", "quality", "api"]


@router.get("/errors", summary="오류 로그 (파이프라인·원천 수집·품질 검사·API)")
async def errors(
    request: Request,
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
    source: SOURCE = "all",
    includeResolved: bool = False,  # noqa: N803
    p: Principal = require_scope("read"),
) -> Response:
    async def compute():
        entries: list[dict] = []
        notes: list[str] = []
        since = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=hours)

        def want(s: str) -> bool:
            return source in ("all", s)

        if want("pipeline"):
            dag: ops.DagsterClient = request.app.state.dagster
            try:
                failed = await dag.runs(["FAILURE"], created_after=since.timestamp(), limit=40)
                # 같은 작업·파티션이 나중에 성공했으면 '해결됨' (재시도·재실행으로 복구된 실패)
                last_ok: dict[tuple[str, str | None], float] = {}
                for r in await dag.runs(["SUCCESS"], created_after=since.timestamp(), limit=500):
                    k = (r["jobName"], ops.job_row(r)["partition"])
                    last_ok[k] = max(last_ok.get(k, 0.0), r.get("creationTime") or 0.0)
                sem = asyncio.Semaphore(5)

                async def one(r: dict) -> list[dict]:
                    job = ops.job_row(r)
                    resolved = last_ok.get((r["jobName"], job["partition"]), 0.0) > (r.get("creationTime") or 0.0)
                    if resolved and not includeResolved:
                        return []  # 숨길 항목은 로그도 읽지 않는다
                    async with sem:
                        try:
                            errs = await dag.run_errors(r["runId"])
                        except ops.DagsterUnavailable:
                            # 한 실행의 로그를 못 읽어도 나머지는 보여 준다 (실패 사실은 실행 기록만으로도 알 수 있다)
                            errs = [
                                {
                                    "at": None,
                                    "step": None,
                                    "error": {"message": "실행 로그를 읽지 못했습니다 (Dagster 응답 없음)"},
                                }
                            ]
                    out = ops.run_error_entries(job, errs)
                    for e in out:
                        e["resolved"] = resolved
                    return out

                for part in await asyncio.gather(*(one(r) for r in failed)):
                    entries += part
                for s in (await dag.instigators())["sensors"]:
                    for t in s["sensorState"].get("ticks") or []:
                        if t.get("error") and float(t["timestamp"]) >= since.timestamp():
                            entries.append(
                                {
                                    "id": f"tick:{s['name']}:{t['timestamp']}",
                                    "at": ops.iso(t["timestamp"]),
                                    "level": "ERROR",
                                    "source": "pipeline",
                                    "where": ops.SCHEDULE_LABEL.get(s["name"], s["name"]),
                                    "message": ops.redact(t["error"]["message"]).splitlines()[0][:300],
                                    "detail": ops.redact(t["error"]["message"]),
                                    "ref": None,
                                    "resolved": False,
                                }
                            )
            except ops.DagsterUnavailable:
                notes.append("오케스트레이터(Dagster)에 연결할 수 없어 파이프라인 실행 오류는 빠졌습니다.")

        if want("ingest"):
            rows = await _pg(
                request,
                """
                SELECT deal_ym, status, last_error, count(*) AS n, max(updated_at) AS at,
                       (array_agg(sgg_cd ORDER BY sgg_cd))[1:8] AS sample
                FROM ops.ingest_partition
                WHERE status IN ('RETRY','QUARANTINED') AND last_error IS NOT NULL AND updated_at >= %s
                GROUP BY deal_ym, status, last_error ORDER BY at DESC LIMIT 200""",
                (since,),
            )
            for r in rows:
                label = "격리" if r["status"] == "QUARANTINED" else "재시도 대기"
                ym = f"{r['deal_ym'][:4]}-{r['deal_ym'][4:]}"
                entries.append(
                    {
                        "id": f"ingest:{r['deal_ym']}:{r['status']}:{zlib.crc32(r['last_error'].encode()):08x}",
                        "at": _iso(r["at"]),
                        "level": "ERROR" if r["status"] == "QUARANTINED" else "WARN",
                        "source": "ingest",
                        "where": f"원천 수집 · {ym} · 시군구 {r['n']}개 {label}",
                        "message": ops.redact(r["last_error"]).splitlines()[0][:300],
                        "detail": ops.redact(r["last_error"])
                        + f"\n\n대상 시군구(최대 8개): {', '.join(r['sample'])}"
                        + (f" 외 {r['n'] - len(r['sample'])}개" if r["n"] > len(r["sample"]) else ""),
                        "ref": None,
                        "resolved": False,
                    }
                )
            ex = await _pg(
                request,
                """SELECT day, exhausted_reason, exhausted_at FROM ops.api_budget
                                       WHERE source='rtms' AND exhausted_at >= %s""",
                (since,),
            )
            for r in ex:
                entries.append(
                    {
                        "id": f"budget:{r['day']}",
                        "at": _iso(r["exhausted_at"]),
                        "level": "WARN",
                        "source": "ingest",
                        "where": f"원천 호출 예산 · {r['day']}",
                        "message": f"원천이 일일 한도 초과를 알림 — 그날 수집 중단, KST 자정 뒤 자동 재개 ({r['exhausted_reason']})",
                        "detail": "공공데이터포털 한도는 인증키 단위라 같은 키를 쓰는 다른 프로그램의 호출도 합산됩니다.",
                        "ref": None,
                        "resolved": False,
                    }
                )

        if want("quality"):
            rows = await _pg(
                request,
                """
                SELECT d.check_id, d.asset, d.partition, d.check_name, d.severity, d.blocking, d.metric, d.at,
                       EXISTS (SELECT 1 FROM ops.dq_result x
                               WHERE x.asset = d.asset AND x.partition IS NOT DISTINCT FROM d.partition
                                 AND x.check_name = d.check_name AND x.at > d.at AND x.passed) AS resolved
                FROM ops.dq_result d
                WHERE NOT d.passed AND d.at >= %s ORDER BY d.at DESC LIMIT 400""",
                (since,),
            )
            for r in rows:
                if r["resolved"] and not includeResolved:
                    continue
                entries.append(
                    {
                        "id": f"dq:{r['check_id']}",
                        "at": _iso(r["at"]),
                        "level": "ERROR" if r["blocking"] else "WARN",
                        "source": "quality",
                        "where": f"{r['asset']} · {r['partition'] or '-'}",
                        "message": f"품질 검사 실패: {r['check_name']}"
                        + (" (차단 — 하위 반영 멈춤)" if r["blocking"] else ""),
                        "detail": ops.redact(json.dumps(r["metric"], ensure_ascii=False, indent=2)),
                        "ref": None,
                        "resolved": bool(r["resolved"]),
                    }
                )

        api_summary: list[dict] = []
        if want("api"):
            ch = request.app.state.res.ch
            rows = list(
                (
                    await ch.query(
                        """SELECT at, route, status, latency_ms, trace_id FROM usage_event
                   WHERE status >= 500 AND at >= now() - toIntervalHour({h:UInt16}) ORDER BY at DESC LIMIT 100""",
                        parameters={"h": hours},
                    )
                ).named_results()
            )
            for r in rows:
                at = r["at"] if r["at"].tzinfo else r["at"].replace(tzinfo=dt.UTC)
                entries.append(
                    {
                        "id": f"api:{r['trace_id']}",
                        "at": at.isoformat(),
                        "level": "ERROR",
                        "source": "api",
                        "where": f"API {r['route']}",
                        "message": f"HTTP {r['status']} · {r['latency_ms']} ms",
                        "detail": f"traceId={r['trace_id']} (서버 로그와 대조: docker compose logs api | grep {r['trace_id']})",
                        "ref": r["trace_id"],
                        "resolved": False,
                    }
                )
            api_summary = [
                {"route": r["route"], "status": r["status"], "count": r["n"]}
                for r in (
                    await ch.query(
                        """SELECT route, status, count() AS n FROM usage_event
                       WHERE status >= 400 AND at >= now() - toIntervalHour({h:UInt16})
                       GROUP BY route, status ORDER BY n DESC LIMIT 20""",
                        parameters={"h": hours},
                    )
                ).named_results()
            ]

        entries.sort(key=lambda e: e["at"] or "", reverse=True)
        counts: dict[str, int] = {}
        for e in entries:
            counts[e["source"]] = counts.get(e["source"], 0) + 1
        return {
            "window": {"hours": hours, "since": since.isoformat()},
            "counts": counts,
            "entries": entries[:500],
            "truncated": len(entries) > 500,
            "apiErrorSummary": api_summary,
            "notes": notes,
        }, 0

    return await respond(request, "ops_errors", {"h": hours, "s": source, "r": includeResolved}, compute, cache=False)


def dagster_url() -> str:
    return settings().dagster_graphql_url
