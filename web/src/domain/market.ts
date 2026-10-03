// 시장 개요 지도의 규칙 (React·차트 무관): 지표 정의, 시군구 값, 색 구간.
import type { SggSummary } from "../api/types";
import { num } from "../lib/format";

export type Metric = "median" | "medianYoY" | "trades" | "cancelRate";
export const METRICS: { value: Metric; label: string; unit: string; title: string }[] = [
  { value: "median", label: "m²당 중위가", unit: "만원/m²", title: "해제·이상치 제외 m²당 거래가 중위수" },
  { value: "medianYoY", label: "가격 변화(전년비)", unit: "%", title: "전년 같은 달 대비 중위수 변화율 — 두 달 모두 표본 30건 이상" },
  { value: "trades", label: "거래량", unit: "건", title: "해제 제외 신고 건수" },
  { value: "cancelRate", label: "해제율", unit: "%", title: "해제 건수 ÷ 신고 건수" },
];

/** URL 의 지표 이름 — 모르는 값이면 기본(중위가). 이름·단위와 칠하는 값이 어긋나지 않게 한곳에서 정한다. */
export function metricOf(v: string | null): Metric {
  return METRICS.find((m) => m.value === v)?.value ?? "median";
}

/** 지도에 칠할 값. 표본 부족 중위가·신고 0건 해제율은 '자료 없음'(null). */
export function metricValue(s: SggSummary | undefined, metric: Metric): number | null {
  if (!s) return null;
  if (metric === "median") return s.lowSample ? null : s.median;
  if (metric === "medianYoY") return s.medianYoY;
  if (metric === "trades") return s.trades;
  return s.trades + s.cancelled > 0 ? s.cancelRate : null;
}

/** 분위 경계 (n 등분, 같은 값 경계는 하나로). 값이 n 개보다 적으면 경계 없음. */
export function quantileBins(values: number[], n: number): number[] {
  const v = [...values].sort((a, b) => a - b);
  if (v.length < n) return [];
  const edges: number[] = [];
  for (let i = 1; i < n; i++) edges.push(v[Math.floor((i * v.length) / n)]);
  return [...new Set(edges)];
}

function binLabel(x: number, metric: Metric): string {
  return metric === "trades" ? num(x) : metric === "median" ? num(x) : num(x, 1);
}

export type Piece = { lt?: number; lte?: number; gt?: number; gte?: number; label: string; color: string };

/**
 * 지도 범례 구간. 변화율은 고정 구간(−5·−1·+1·+5%, 0 근처는 중립색), 나머지는 그 달 값의 6분위.
 * colors.div = 발산 5색(하락 → 중립 → 상승), colors.seq = 순차 8색(옅음 → 진함).
 */
export function mapPieces(values: number[], metric: Metric, colors: { div: string[]; seq: string[] }): Piece[] {
  if (metric === "medianYoY") {
    const d = colors.div;
    return [
      { lt: -5, label: "−5% 미만", color: d[0] }, { gte: -5, lt: -1, label: "−5 ~ −1%", color: d[1] },
      { gte: -1, lte: 1, label: "−1 ~ +1%", color: d[2] }, { gt: 1, lte: 5, label: "+1 ~ +5%", color: d[3] },
      { gt: 5, label: "+5% 초과", color: d[4] },
    ];
  }
  const edges = quantileBins(values, 6);
  const ramp = [colors.seq[1], colors.seq[2], colors.seq[3], colors.seq[4], colors.seq[5], colors.seq[7]];
  const bounds = [Number.NEGATIVE_INFINITY, ...edges, Number.POSITIVE_INFINITY];
  return bounds.slice(0, -1).map((lo, i) => {
    const hi = bounds[i + 1];
    const label = lo === Number.NEGATIVE_INFINITY ? `${binLabel(hi, metric)} 미만`
      : hi === Number.POSITIVE_INFINITY ? `${binLabel(lo, metric)} 이상` : `${binLabel(lo, metric)} ~ ${binLabel(hi, metric)}`;
    return { ...(lo === Number.NEGATIVE_INFINITY ? {} : { gte: lo }), ...(hi === Number.POSITIVE_INFINITY ? {} : { lt: hi }), label, color: ramp[Math.min(i, ramp.length - 1)] };
  });
}
