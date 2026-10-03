// 단지 화면의 규칙 (React 무관): 전용면적 구간별 거래 이력과 중위가.
import type { ComplexDetail } from "../api/types";
import { quantile } from "../lib/format";

export const COMPLEX_BANDS = [
  { key: "all", label: "전체", lo: 0, hi: Infinity }, { key: "s", label: "60m² 미만", lo: 0, hi: 60 },
  { key: "m", label: "60~85m²", lo: 60, hi: 85 }, { key: "l", label: "85~135m²", lo: 85, hi: 135 }, { key: "xl", label: "135m² 이상", lo: 135, hi: Infinity },
];
type History = ComplexDetail["history"];
type Band = (typeof COMPLEX_BANDS)[number];

export function bandOf(key: string): Band {
  return COMPLEX_BANDS.find((x) => x.key === key) ?? COMPLEX_BANDS[0];
}
/** 그 구간의 거래 (면적 lo 이상 hi 미만) */
export function inBand(history: History, b: Band): History {
  return history.filter((h) => h[1] >= b.lo && h[1] < b.hi);
}
/** 거래가 있는 구간만 고를 수 있게 ('전체'는 늘) */
export function presentBands(history: History): Band[] {
  return COMPLEX_BANDS.filter((x) => x.key === "all" || history.some((h) => h[1] >= x.lo && h[1] < x.hi));
}
/** 해제·이상치를 뺀 거래의 m²당 중위가와 건수 */
export function validMedian(history: History): { median: number | null; n: number } {
  const valid = history.filter((h) => !h[5] && !h[6]);
  return { median: quantile(valid.map((h) => h[4]), 0.5), n: valid.length };
}
