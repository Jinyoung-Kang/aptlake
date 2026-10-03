// 수집 상태·오류 로그 화면의 규칙 (React 무관): 상태 이름, 큐 순서, 소요 시간, 로그 거르기·내보내기 문구.
import type { LogEntry, OpsJob, OpsStatus } from "../api/types";
import { DASH, duration, kst } from "../lib/format";

export const JOB_STATUS: Record<string, { label: string; cls: string }> = {
  QUEUED: { label: "대기", cls: "wait" }, NOT_STARTED: { label: "시작 전", cls: "wait" }, STARTING: { label: "시작 중", cls: "run" },
  STARTED: { label: "실행 중", cls: "run" }, CANCELING: { label: "취소 중", cls: "wait" }, SUCCESS: { label: "성공", cls: "ok" },
  FAILURE: { label: "실패", cls: "fail" }, CANCELED: { label: "취소", cls: "" },
};
export const LOG_SOURCES = ["all", "pipeline", "ingest", "quality", "api"] as const;
export const SOURCE_LABEL: Record<string, string> = { pipeline: "파이프라인", ingest: "원천 수집", quality: "품질 검사", api: "API" };
export const PRIORITY_TONE: Record<string, "info" | "warn" | "good" | undefined> = { incremental: "info", retry: "warn", "publish-only": "good" };

/** 항목 상태 — 색만이 아니라 글자로도 구분 (미해결 / 이후 해결됨 / 비우기 이전) */
export function entryState(e: LogEntry): { label: string; tone: "bad" | "good" | undefined } {
  if (e.cleared) return { label: "비우기 이전", tone: undefined };
  if (e.resolved) return { label: "이후 해결됨", tone: "good" };
  return { label: "미해결", tone: "bad" };
}

export function jobDuration(j: OpsJob, now: number): string {
  if (j.durationS != null) return duration(j.durationS);
  if (j.startedAt && !j.endedAt) return `${duration((now - new Date(j.startedAt).getTime()) / 1000)} 경과`;
  if (!j.startedAt && j.requestedAt) return `대기 ${duration((now - new Date(j.requestedAt).getTime()) / 1000)}`;
  return DASH;
}

const RUNNING = new Set(["STARTING", "STARTED", "CANCELING"]);
const ts = (x: string | null) => (x ? new Date(x).getTime() : Number.POSITIVE_INFINITY);
/** 진행·대기: 실행 중(먼저 시작한 순) → 대기(요청 순 = 큐에서 나갈 순서). */
export function queueOrder(jobs: OpsJob[]): OpsJob[] {
  return [...jobs].sort((a, b) => {
    const ra = RUNNING.has(a.status) ? 0 : 1, rb = RUNNING.has(b.status) ? 0 : 1;
    if (ra !== rb) return ra - rb;
    return ra === 0 ? ts(a.startedAt) - ts(b.startedAt) : ts(a.requestedAt) - ts(b.requestedAt);
  });
}
/** 최근: 끝난 시각 최신순. */
export function recentOrder(jobs: OpsJob[]): OpsJob[] {
  return [...jobs].sort((a, b) => (b.endedAt ?? "").localeCompare(a.endedAt ?? ""));
}

/** 상단 지표: 실행·대기 수, 수집 진행률(반영 완료 파티션 비율). */
export function pipelineSummary(s: OpsStatus | null) {
  const running = s?.jobs.active.filter((j) => j.status === "STARTED" || j.status === "STARTING").length ?? 0;
  const queued = (s?.jobs.active.length ?? 0) - running;
  const merged = s?.summary.partitions.MERGED ?? 0;
  const total = s?.summary.totalPartitions ?? 0;
  return { running, queued, merged, total, mergedPct: total ? (merged / total) * 100 : 0 };
}

/** 출처·검색어로 거르기 (검색은 위치·메시지·상세에서, 대소문자 무시). */
export function filterEntries(all: LogEntry[], source: string, q: string): LogEntry[] {
  const term = q.trim().toLowerCase();
  return all.filter((e) => (source === "all" || e.source === source) &&
    (!term || `${e.where} ${e.message} ${e.detail}`.toLowerCase().includes(term)));
}

export function entryText(e: LogEntry): string {
  const head = `[${kst(e.at)} KST] ${e.level} [${SOURCE_LABEL[e.source] ?? e.source}] [${entryState(e).label}] ${e.where}`;
  const detail = e.detail && e.detail !== e.message ? `\n${e.detail.split("\n").map((l) => `    ${l}`).join("\n")}` : "";
  return `${head}\n  ${e.message}${detail}`;
}

/** 복사·저장용 전체 로그 (머리글 + 항목). nowIso 는 생성 시각. */
export function logText(entries: LogEntry[], o: { hours: string; source: string; q: string; nowIso: string }): string {
  const head = `# AptLake 오류 로그 — 최근 ${Number(o.hours) >= 24 ? `${Number(o.hours) / 24}일` : `${o.hours}시간`}, 출처 ${o.source === "all" ? "전체" : SOURCE_LABEL[o.source]}` +
    `${o.q.trim() ? `, 검색 "${o.q.trim()}"` : ""}, ${entries.length}건 (생성 ${kst(o.nowIso)} KST)`;
  return [head, ...entries.map(entryText)].join("\n\n");
}

/** 저장 파일 이름: aptlake-errors-YYYYMMDDHHmm.txt (UTC) */
export function logFileName(nowIso: string): string {
  return `aptlake-errors-${nowIso.slice(0, 16).replace(/[-:T]/g, "")}.txt`;
}
