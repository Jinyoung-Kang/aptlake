// 지역 분석 화면의 규칙 (React 무관): 기간 기본값, 빈 달 채우기, 최근 12개월 지표, 대표 시세, 전년비.
import type { Available, MonthRow, PriceDistribution } from "../api/types";
import { monthRange, ymAdd } from "../lib/format";

export const WEB_MAX_MONTHS = 60;
const DEFAULT_SPAN = 24;

/**
 * 조회 기간. 기본 = 최근 24개월(조회 가능 범위 안), 분포는 기본 기준월.
 * 전년비 계산을 위해 표시 기간보다 12개월 앞(fetchFrom)부터 받는다.
 */
export function regionPeriod(params: URLSearchParams, avail: Available | null) {
  const max = avail?.to ?? "";
  const min = avail?.from ?? "";
  const clamp = (ym: string) => (ym < min ? min : ym);
  const to = params.get("to") ?? max;
  const from = params.get("from") ?? (max ? clamp(ymAdd(max, -(DEFAULT_SPAN - 1))) : "");
  const ym = params.get("ym") ?? avail?.default ?? "";
  // 전년 대비 계산용으로 12개월 더 당겨 받되, 받는 기간이 웹 플랜 상한을 넘지 않게 (넘으면 422 — QA-015).
  // 고른 기간이 상한에 가까우면 앞쪽 달의 전년 대비는 비어 보인다.
  const earliest = to ? ymAdd(to, -(WEB_MAX_MONTHS - 1)) : "";
  const wanted = from && min ? clamp(ymAdd(from, -12)) : from;
  const fetchFrom = wanted && earliest && wanted < earliest ? (from < earliest ? from : earliest) : wanted;
  return { min, max, from, to, ym, fetchFrom };
}

const EMPTY: Omit<MonthRow, "dealYm"> = {
  reported: 0, trades: 0, cancelled: 0, sampleSize: 0, outliers: 0, p25PricePerM2: null, medianPricePerM2: null, p75PricePerM2: null,
};
/** 거래가 없는 달도 0건 행으로 채운다 (차트 가로축이 빠지지 않게). */
export function fillMonths(items: MonthRow[], from: string, to: string): MonthRow[] {
  const map = new Map(items.map((r) => [r.dealYm, r]));
  return monthRange(from, to).map((m) => map.get(m) ?? ({ dealYm: m, ...EMPTY } as MonthRow));
}

/** 전년 같은 달 대비 중위가 변화율(%) — 두 달 모두 값이 있을 때만. */
export function medianYoY(cur: MonthRow, prev: MonthRow | undefined): number | null {
  return prev?.medianPricePerM2 && cur.medianPricePerM2 ? (cur.medianPricePerM2 / prev.medianPricePerM2 - 1) * 100 : null;
}

/** 대표 시세: 표본이 충분한 달 중 잠정이 아닌 최근 달(없으면 최근 달)과 그 전년비. */
export function latestQuote(full: MonthRow[]): { cur: MonthRow; yoy: number | null } | null {
  const valid = full.filter((r) => r.medianPricePerM2 != null && !r.lowSample);
  const cur = [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1];
  if (!cur) return null;
  return { cur, yoy: medianYoY(cur, full.find((r) => r.dealYm === ymAdd(cur.dealYm, -12))) };
}

/** 표시 기간의 최근 12개월 지표 (거래·해제율·중위가 범위)와 최근 확정 월. */
export function recentStats(rows: MonthRow[]) {
  const valid = rows.filter((r) => r.medianPricePerM2 != null);
  const last12 = rows.slice(-12);
  const trades = last12.reduce((a, r) => a + r.trades, 0);
  const cancelled = last12.reduce((a, r) => a + r.cancelled, 0);
  const medians = last12.map((r) => r.medianPricePerM2).filter((v): v is number => v != null);
  return {
    trades,
    cancelRate: trades + cancelled ? (cancelled / (trades + cancelled)) * 100 : null,
    medianRange: medians.length ? { lo: Math.min(...medians), hi: Math.max(...medians), n: medians.length } : null,
    lastConfirmed: [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1],
  };
}

/** 산점도 점을 정상·해제·이상치로 [면적, 금액, 층] */
export function splitPoints(points: PriceDistribution["points"]) {
  const xyz = (x: PriceDistribution["points"][number]) => [x[0], x[1], x[2]];
  return {
    normal: points.filter((x) => !x[3] && !x[4]).map(xyz),
    cancelled: points.filter((x) => x[3]).map(xyz),
    outlier: points.filter((x) => !x[3] && x[4]).map(xyz),
  };
}
