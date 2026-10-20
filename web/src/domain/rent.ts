// 전월세 화면의 규칙 (React 무관, ADR-050): 빈 달 채우기, 최근 12개월 지표, 대표 전세가율, 목록 조건, 이력 표시.
import type { RentQuery } from "../api/endpoints";
import type { Available, JeonseRatioRow, RentMonthRow } from "../api/types";
import { lastDay, monthRange } from "../lib/format";
import { AREA_BANDS, PAGE_SIZE } from "./trades";

const EMPTY: Omit<RentMonthRow, "dealYm"> = {
  contracts: 0, jeonse: 0, wolse: 0, renewals: 0, newContracts: 0, typeUnknown: 0, renewalRightUsed: 0, jeonseSample: 0, outliers: 0,
  jeonseP25PerM2: null, jeonseMedianPerM2: null, jeonseP75PerM2: null, wolseMedianRent: null, wolseMedianDeposit: null,
};
/** 계약이 없는 달도 0건 행으로 채운다 (차트 가로축이 빠지지 않게). */
export function fillRentMonths(items: RentMonthRow[], from: string, to: string): RentMonthRow[] {
  const map = new Map(items.map((r) => [r.dealYm, r]));
  return monthRange(from, to).map((m) => map.get(m) ?? ({ dealYm: m, ...EMPTY } as RentMonthRow));
}

/** 비율(%) — 분모가 0 이면 null */
const share = (a: number, b: number) => (b > 0 ? (a / b) * 100 : null);

/**
 * 표시 기간의 최근 12개월 지표. 갱신 비율은 계약 구분이 적힌 계약(갱신 + 신규) 중 갱신 —
 * 원천이 비운 '미상'은 분모에서 뺀다(신규로 세지 않는다).
 */
export function rentStats(rows: RentMonthRow[]) {
  const last12 = rows.slice(-12);
  const sum = (f: (r: RentMonthRow) => number) => last12.reduce((a, r) => a + f(r), 0);
  const contracts = sum((r) => r.contracts);
  const jeonse = sum((r) => r.jeonse);
  const renewals = sum((r) => r.renewals);
  const typed = renewals + sum((r) => r.newContracts);
  const valid = rows.filter((r) => r.jeonseMedianPerM2 != null && !r.lowSample);
  return {
    contracts,
    jeonseShare: share(jeonse, contracts),
    renewalShare: share(renewals, typed),
    typeUnknownShare: share(sum((r) => r.typeUnknown), contracts),
    rrUsed: sum((r) => r.renewalRightUsed),
    lastConfirmed: [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1],
  };
}

/** 대표 전세가율: 값이 있는 달 중 잠정이 아닌 최근 달 (없으면 값이 있는 최근 달). */
export function latestRatio(items: JeonseRatioRow[]): JeonseRatioRow | undefined {
  const valid = items.filter((r) => r.ratioPct != null);
  return [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1];
}

export const KINDS = [{ key: "", label: "전체" }, { key: "jeonse", label: "전세" }, { key: "wolse", label: "월세" }] as const;
export type Kind = "" | "jeonse" | "wolse";

/** URL 조건 → 전월세 목록 조회 조건. 기간 기본 = 기본 기준월 한 달. 기준월을 아직 모르면 query=null. */
export function rentFilter(params: URLSearchParams, avail: Available | null, fallbackSgg: string) {
  const sgg = params.get("sgg") ?? fallbackSgg;
  const base = avail?.default ?? "";
  const from = params.get("from") ?? (base ? `${base}-01` : "");
  const to = params.get("to") ?? (base ? lastDay(base) : "");
  const area = params.get("area") ?? "";
  const k = params.get("kind");
  const kind: Kind = k === "jeonse" || k === "wolse" ? k : "";
  const band = AREA_BANDS.find((a) => a.key === area) ?? AREA_BANDS[0];
  const query: RentQuery | null = from && to
    ? { sgg, from, to, limit: PAGE_SIZE, kind: kind || undefined, minArea: band.min, maxArea: band.max } : null;
  return { sgg, from, to, area, kind, query };
}

/** 보증금·월세 한 줄: 전세 "보증금", 월세 "보증금 / 월세" (만원 단위 값 → 표시 함수에 맡김) */
export function rentPrice(kind: "jeonse" | "wolse", deposit: string, rent: string): string {
  return kind === "jeonse" ? deposit : `${deposit} / 월 ${rent}`;
}

export const RENT_VERSION_FIELD: Record<string, string> = {
  contractType: "계약 구분", contractTerm: "계약 기간", renewalRightUsed: "갱신요구권",
  previousDepositManwon: "종전 보증금(만원)", previousMonthlyRentManwon: "종전 월세(만원)", buildYear: "건축년도",
};
