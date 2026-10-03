// 가격지수 화면의 규칙 (React 무관): 대표값, R-ONE 과 같은 기준으로 맞추기, 상관 해석 문구.
import type { IndexSummaryItem, RegionIndex } from "../api/types";

export type Span = "12" | "24" | "36" | "all";

/** 대표값 = 확정된 최근 달 (잠정 달은 신고가 더 들어오며 바뀐다). 확정 달이 없으면 최근 달. */
export function representative(item: IndexSummaryItem) {
  return item.confirmed ?? item;
}

/**
 * 표시 구간에서 R-ONE 과 겹치는 첫 달을 두 지수 모두 100 으로 맞춘다 — 기준 시점이 달라도 축 하나로 움직임을 비교.
 * 겹치는 달이 없으면 자체 지수 그대로, R-ONE 은 빈 값.
 */
export function rebased(data: RegionIndex, span: Span) {
  const series = span === "all" ? data.series : data.series.slice(-Number(span));
  const ref = new Map(data.reference.series.map((r) => [r.period, r.value]));
  const b = series.find((p) => ref.has(p.period));
  const k = b ? 100 / b.value : 1;
  const kr = b ? 100 / (ref.get(b.period) as number) : 1;
  return {
    base: b?.period ?? null,
    rows: series.map((p) => ({ ...p, v: p.value * k, lo: p.ciLow * k, hi: p.ciHigh * k, ref: b && ref.has(p.period) ? (ref.get(p.period) as number) * kr : null })),
  };
}

/** 월간 변화율 상관계수 → 말 (임계값 0.8·0.6·0.3) */
export function corrText(c: number | null | undefined): string {
  return c == null ? "비교 기간 부족" : c >= 0.8 ? "매우 비슷하게 움직임" : c >= 0.6 ? "대체로 비슷함" : c >= 0.3 ? "약하게 비슷함" : "차이가 큼";
}
