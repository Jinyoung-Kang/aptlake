// 데이터 품질 화면의 규칙 (React·차트 무관): 상태 이름·순서, 원천 스키마 판, 완결도, 히트맵 칸.
import type { FailedCheck, LatencyMetric, QualityGrid, QualityRollup } from "../api/types";
import { num } from "../lib/format";

export const STATUS_LABEL: Record<string, string> = {
  MERGED: "반영 완료", LOADED: "적재·반영 대기", PENDING: "미수집", FETCHING: "수집 중", RETRY: "재시도 대기", QUARANTINED: "격리",
};
/** 범례·색 순서 (진행 순) */
export const STATUS_ORDER = ["MERGED", "LOADED", "FETCHING", "PENDING", "RETRY", "QUARANTINED"];
/** 상태 → 의미 색 이름 (팔레트의 status 키) */
export const STATUS_TONE: Record<string, "good" | "info" | "none" | "warn" | "bad"> = {
  MERGED: "good", LOADED: "info", FETCHING: "info", PENDING: "none", RETRY: "warn", QUARANTINED: "bad",
};

/** 원천 스키마 판 이름: 'empty' = 마지막 응답이 0건이라 판을 가릴 수 없음, 'unrecorded' = 판 기록 이전 적재 */
export function schemaVersionLabel(v: string): string {
  return v === "empty" ? "빈 응답" : v === "unrecorded" ? "판 기록 전" : `v${v}`;
}

/** 판별 파티션 수 한 줄 — '판 이름 건수' 를 ' · ' 로 */
export function schemaVersionText(versions: Record<string, number>): string {
  return Object.entries(versions).map(([v, n]) => `${schemaVersionLabel(v)} ${num(n)}`).join(" · ");
}

/** 파티션(시군구×월) 반영 완료 비율 */
export function completion(partitions: Record<string, number>) {
  const total = Object.values(partitions).reduce((a, b) => a + b, 0);
  const merged = partitions.MERGED ?? 0;
  return { total, merged, pct: total ? (merged / total) * 100 : 0 };
}

/** '현재 실패 중' = 이후 통과하지 않은 검사. withResolved 면 최근 7일 전부. */
export function failingChecks(all: FailedCheck[], withResolved: boolean): FailedCheck[] {
  return all.filter((f) => withResolved || !f.resolved);
}

/** 집계에 나타난 계약월(YYYYMM) 오름차순 */
export function rollupMonths(rollup: QualityRollup | null): string[] {
  const set = new Set<string>();
  for (const m of Object.values(rollup?.cells ?? {})) for (const k of Object.keys(m)) set.add(k);
  return [...set].sort();
}

/** 시도 히트맵 칸: [x(월), y(시도), 반영 완료 %, 상태별 개수 문구] */
export function rollupCells(rollup: QualityRollup | null, sidoCodes: string[], months: string[]): [number, number, number, string][] {
  const out: [number, number, number, string][] = [];
  sidoCodes.forEach((sd, y) => {
    months.forEach((m, x) => {
      const c = rollup?.cells[sd]?.[m];
      if (!c) return;
      const tot = Object.values(c).reduce((a, b) => a + b, 0);
      out.push([x, y, Math.round(((c.MERGED ?? 0) / tot) * 100), Object.entries(c).map(([k, v]) => `${STATUS_LABEL[k] ?? k} ${v}`).join(" · ")]);
    });
  });
  return out;
}

/** 시군구 히트맵 칸: [x(월), y(시군구), 상태 순번, 원천 건수] */
export function gridCells(grid: QualityGrid | null, sggCodes: string[], months: string[]): [number, number, number, number | null][] {
  const out: [number, number, number, number | null][] = [];
  sggCodes.forEach((code, y) => {
    months.forEach((m, x) => {
      const c = grid?.cells[code]?.[m];
      if (c) out.push([x, y, STATUS_ORDER.indexOf(c[0]), c[1]]);
    });
  });
  return out;
}

/** URL 의 'sgg:YYYYMM' → [시군구, 'YYYY-MM'] (형식이 아니면 null) */
export function parseCell(cell: string | null, known: (sgg: string) => boolean): [string, string] | null {
  if (!cell) return null;
  const [code, m] = cell.split(":");
  return known(code) && /^\d{6}$/.test(m ?? "") ? [code, `${m.slice(0, 4)}-${m.slice(4)}`] : null;
}

// ───── 원천 반영 지연 (ADR-046) ─────
export type LatencyKey = "L1" | "L2" | "L3";
export const LATENCY_LABEL: Record<LatencyKey, string> = { L1: "신고 반영", L2: "해제 반영", L3: "수집→발행" };
export const LATENCY_DESC: Record<LatencyKey, string> = {
  L1: "거래가 처음 수집된 날 − 계약일",
  L2: "해제가 처음 수집된 날 − 해제일",
  L3: "수집한 내용이 서빙에 발행되기까지",
};
export const EXCLUDED_LABEL: Record<string, string> = {
  initial_load: "첫 수집(초기 적재)에 이미 있음",
  low_resolution: "계약월 3개월 이후 관측 (하루 해상도 아님)",
  time_inverted: "시각 역전 버전",
  no_base_date: "기준일 없음",
  negative_lag: "기준일이 관측일보다 늦음",
};

/** 일 단위 지연 → 막대 구간 (뒤로 갈수록 넓게). 구간 경계는 화면 표시용이고 분위수는 API 가 원 분포로 계산한다 */
const DAY_BINS: [number, number, string][] = [
  [0, 0, "0일"], [1, 1, "1일"], [2, 3, "2–3일"], [4, 7, "4–7일"], [8, 14, "8–14일"],
  [15, 30, "15–30일"], [31, 60, "31–60일"], [61, 90, "61–90일"], [91, Number.POSITIVE_INFINITY, "91일+"],
];

/** 분 단위 구간 상한 → 이름 */
function minuteLabel(upper: number | null, prev: number | null): string {
  const f = (m: number) => (m < 60 ? `${m}분` : m < 1440 ? `${m / 60}시간` : `${m / 1440}일`);
  if (upper == null) return `${prev == null ? "" : f(prev)} 초과`;
  return `${f(upper)} 이하`;
}

/** 지표 분포 → [구간 이름, 건수] (표·막대 공용) */
export function latencyBars(m: LatencyMetric): [string, number][] {
  if (m.unit === "minute") {
    return m.distribution.map(([upper, n], i) => [minuteLabel(upper, i ? m.distribution[i - 1][0] : null), n]);
  }
  return DAY_BINS.map(([lo, hi, label]) => [label, m.distribution.reduce((a, [d, n]) => (d != null && d >= lo && d <= hi ? a + n : a), 0)]);
}

/** 분위수 표시 — 표본이 없으면 숫자를 보이지 않는다 */
export function latencyValue(m: LatencyMetric, v: number | null): string {
  if (!m.n || v == null) return "측정 중";
  if (m.unit === "day") return `${v}일`;
  return v < 60 ? `${v}분` : `${(v / 60).toFixed(1)}시간`;
}

/** 제외 사유 [이름, 건수] (많은 순) */
export function latencyExcluded(m: LatencyMetric): [string, number][] {
  return Object.entries(m.excluded).sort((a, b) => b[1] - a[1]).map(([k, n]) => [EXCLUDED_LABEL[k] ?? k, n]);
}

/** 최근 days 일 (KST 날짜, 오늘 포함) */
export function latencyWindow(now: Date, days = 30): { from: string; to: string } {
  const kst = (t: number) => new Date(t + 9 * 3600_000).toISOString().slice(0, 10);
  return { from: kst(now.getTime() - (days - 1) * 86400_000), to: kst(now.getTime()) };
}
