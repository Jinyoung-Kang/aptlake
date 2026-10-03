import { describe, expect, it } from "vitest";
import type { LogEntry, OpsJob } from "../api/types";
import { entryState, entryText, filterEntries, jobDuration, logFileName, logText, pipelineSummary, queueOrder, recentOrder } from "./ops";

const job = (o: Partial<OpsJob>): OpsJob => ({
  runId: "r", job: "month_pipeline", jobLabel: "월 수집", partition: null, priority: null, priorityLabel: "수동", trigger: "수동",
  status: "QUEUED", requestedAt: null, startedAt: null, endedAt: null, durationS: null, ...o,
});
const entry = (o: Partial<LogEntry>): LogEntry => ({
  id: "e", at: "2026-10-01T00:00:00+00:00", level: "ERROR", source: "pipeline", where: "월 수집 · 202407", message: "boom",
  detail: "boom", ref: null, resolved: false, ...o,
});

describe("작업 큐", () => {
  it("실행 중(먼저 시작한 순) → 대기(먼저 요청한 순)", () => {
    const jobs = [
      job({ runId: "q2", requestedAt: "2026-10-01T00:02:00Z" }),
      job({ runId: "r2", status: "STARTED", startedAt: "2026-10-01T00:05:00Z" }),
      job({ runId: "q1", requestedAt: "2026-10-01T00:01:00Z" }),
      job({ runId: "r1", status: "STARTING", startedAt: "2026-10-01T00:03:00Z" }),
    ];
    expect(queueOrder(jobs).map((j) => j.runId)).toEqual(["r1", "r2", "q1", "q2"]);
    expect(jobs[0].runId).toBe("q2");  // 원본은 그대로
  });
  it("최근 작업은 끝난 시각 최신순, 끝나지 않은 것은 뒤로", () => {
    const jobs = [job({ runId: "a", endedAt: "2026-10-01T01:00:00Z" }), job({ runId: "b" }), job({ runId: "c", endedAt: "2026-10-01T02:00:00Z" })];
    expect(recentOrder(jobs).map((j) => j.runId)).toEqual(["c", "a", "b"]);
  });
  it("소요: 끝났으면 걸린 시간, 실행 중이면 경과, 대기 중이면 대기 시간", () => {
    const now = Date.parse("2026-10-01T00:10:00Z");
    expect(jobDuration(job({ durationS: 75 }), now)).toBe("1분 15초");
    expect(jobDuration(job({ startedAt: "2026-10-01T00:08:00Z" }), now)).toBe("2분 경과");
    expect(jobDuration(job({ requestedAt: "2026-10-01T00:09:30Z" }), now)).toBe("대기 30초");
    expect(jobDuration(job({}), now)).toBe("–");
  });
  it("실행·대기 수와 반영 완료 비율", () => {
    expect(pipelineSummary(null)).toEqual({ running: 0, queued: 0, merged: 0, total: 0, mergedPct: 0 });
  });
});

describe("오류 로그", () => {
  it("상태는 글자로: 비우기 이전 > 이후 해결됨 > 미해결", () => {
    expect(entryState(entry({ cleared: true, resolved: true })).label).toBe("비우기 이전");
    expect(entryState(entry({ resolved: true }))).toEqual({ label: "이후 해결됨", tone: "good" });
    expect(entryState(entry({}))).toEqual({ label: "미해결", tone: "bad" });
  });
  it("출처·검색어(위치·메시지·상세, 대소문자 무시)로 거른다", () => {
    const all = [entry({ id: "1", source: "api", message: "HTTP 503" }), entry({ id: "2", detail: "Trino OOM\nstack" })];
    expect(filterEntries(all, "api", "").map((e) => e.id)).toEqual(["1"]);
    expect(filterEntries(all, "all", "  trino ").map((e) => e.id)).toEqual(["2"]);
  });
  it("복사 문구: 머리글(기간·출처·검색·건수) + 항목, 상세는 메시지와 다를 때만 들여 써서", () => {
    const e = entry({ detail: "boom\nat x" });
    expect(entryText(e)).toBe("[2026-10-01 09:00:00 KST] ERROR [파이프라인] [미해결] 월 수집 · 202407\n  boom\n    boom\n    at x");
    expect(entryText(entry({}))).toBe("[2026-10-01 09:00:00 KST] ERROR [파이프라인] [미해결] 월 수집 · 202407\n  boom");
    const text = logText([e], { hours: "168", source: "pipeline", q: " x ", nowIso: "2026-10-01T00:30:00Z" });
    expect(text.split("\n")[0]).toBe('# AptLake 오류 로그 — 최근 7일, 출처 파이프라인, 검색 "x", 1건 (생성 2026-10-01 09:30:00 KST)');
    expect(logFileName("2026-10-01T00:30:12.000Z")).toBe("aptlake-errors-202610010030.txt");
  });
});
