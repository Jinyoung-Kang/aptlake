// 전월세 화면의 규칙 (React 무관, ADR-050): 빈 달 채우기, 최근 12개월 지표, 대표 전세가율, 목록 조건, 이력 표시.
import type { RentQuery } from "../api/endpoints";
import type { Available, JeonseRatioRow, RentMonthRow } from "../api/types";
import { lastDay, manwon, monthRange } from "../lib/format";
import { AREA_BANDS, PAGE_SIZE } from "./trades";

const EMPTY: Omit<RentMonthRow, "dealYm"> = {
  contracts: 0, jeonse: 0, wolse: 0, renewals: 0, newContracts: 0, typeUnknown: 0, renewalRightUsed: 0, jeonseSample: 0, outliers: 0,
  jeonseP25PerM2: null, jeonseMedianPerM2: null, jeonseP75PerM2: null, wolseMedianRent: null, wolseMedianDeposit: null,
};
/**
 * 월 행 + 수집·발행 상태 (화면 전용).
 *  - uncollected: 전월세 자료가 발행된 적이 없고(API uncollectedMonths) 행도 없는 달 — 숫자를 0 이 아니라 '자료 없음'으로 그린다
 */
export type RentMonthCell = RentMonthRow & { uncollected?: boolean };

/**
 * 표시 기간의 모든 달을 채운다 (차트 가로축이 빠지지 않게). 응답에 행이 없는 달 중 API 의 uncollectedMonths(자료가
 * 발행된 적이 없는 달)는 계약 0건이 아니므로 빈칸으로 두고, 그 밖의 행 없는 달은 계약 0건이다.
 * 행이 있는 달은 목록에 있어도 그 값을 그대로 쓴다 — 완결은 '그 달 자료가 발행된 적이 있는가'라, 서빙에 행이 있는 달이
 * 목록에 있는 것은 발행 기록이 서빙 교체를 아직 따라오지 못한 때(발행 도중 등)뿐이다. 이 목록은 0건과 '자료 없음'을 가르는 데만 쓴다.
 */
export function fillRentMonths(items: RentMonthRow[], from: string, to: string, uncollected: string[] = []): RentMonthCell[] {
  const map = new Map(items.map((r) => [r.dealYm, r]));
  const pending = new Set(uncollected);
  return monthRange(from, to).map((m) => map.get(m) ?? { dealYm: m, ...EMPTY, ...(pending.has(m) ? { uncollected: true } : {}) });
}

/** 차트 값: 수집·발행 전인 달은 null (선·막대가 끊긴다) */
export function cellValue<T>(r: RentMonthCell, v: T): T | null {
  return r.uncollected ? null : v;
}

/** 전세가율을 내지 않은 까닭 (지역 complete=false · 단지 window.complete=false 공용) — API 의 완결 = 그 달 자료가 발행된 적이 있음 */
export const RATIO_INCOMPLETE_NOTE = "창 안에 매매·전월세 자료가 발행된 적이 없는 달(수집 전·첫 수집 실패·첫 발행 전)이 있어 비율을 내지 않음";
/** 단지 창이 미완결(window.complete=false)일 때 계약 수의 기준 — API 는 그때도 발행된 계약만 센다 */
export const PUBLISHED_BASIS = "발행된 계약 기준";
/**
 * 단지 창이 미완결인데 구간이 없을 때(계약 0건)의 까닭. complete 는 매매·전월세를 합친 판정이라
 * '전월세 달이 빠졌다'고 단정하지 않고 0건을 확정하지 않는다는 데까지만 적는다.
 */
export const COUNT_INCOMPLETE_NOTE = "창 안에 매매·전월세 자료가 발행된 적이 없는 달(수집 전·첫 수집 실패·첫 발행 전)이 있어 0건이 확정은 아님";

/** 비율(%) — 분모가 0 이면 null */
const share = (a: number, b: number) => (b > 0 ? (a / b) * 100 : null);

/**
 * 표시 기간의 최근 12개월 지표. 갱신 비율은 계약 구분이 적힌 계약(갱신 + 신규) 중 갱신 —
 * 원천이 비운 '미상'은 분모에서 뺀다(신규로 세지 않는다).
 */
export function rentStats(rows: RentMonthCell[]) {
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
    /** 최근 12개월 중 수집·발행 전인 달 수 — 합계에서 빠져 있다 */
    uncollected: last12.filter((r) => r.uncollected).length,
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

/** 전월세 버전 이력의 값 표시 — 원천이 비운 값(API null·빈 문자열)은 '미상' (매매 이력의 '없음'과 다르다) */
export const rentVersionValue = (v: unknown): string => (v == null || v === "" ? "미상" : String(v));
/** 계약 상세의 금액(만원, 종전 보증금·월세) — 원천이 비운 값은 버전 이력과 같게 '미상'. 0·음수는 값 그대로 (manwon 이 음수를 '-' 부호로 그린다) */
export const rentAmount = (v: number | null | undefined): string => (v == null ? "미상" : manwon(v));

export const RENT_VERSION_FIELD: Record<string, string> = {
  contractType: "계약 구분", contractTerm: "계약 기간", renewalRightUsed: "갱신요구권",
  previousDepositManwon: "종전 보증금(만원)", previousMonthlyRentManwon: "종전 월세(만원)", buildYear: "건축년도",
};
