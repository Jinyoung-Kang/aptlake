// 데이터 품질 화면의 규칙 (React·차트 무관): 상태 이름·순서, 완결도, 히트맵 칸.
import type { FailedCheck, QualityGrid, QualityRollup } from "../api/types";

export const STATUS_LABEL: Record<string, string> = {
  MERGED: "반영 완료", LOADED: "적재·반영 대기", PENDING: "미수집", FETCHING: "수집 중", RETRY: "재시도 대기", QUARANTINED: "격리",
};
/** 범례·색 순서 (진행 순) */
export const STATUS_ORDER = ["MERGED", "LOADED", "FETCHING", "PENDING", "RETRY", "QUARANTINED"];
/** 상태 → 의미 색 이름 (팔레트의 status 키) */
export const STATUS_TONE: Record<string, "good" | "info" | "none" | "warn" | "bad"> = {
  MERGED: "good", LOADED: "info", FETCHING: "info", PENDING: "none", RETRY: "warn", QUARANTINED: "bad",
};

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
