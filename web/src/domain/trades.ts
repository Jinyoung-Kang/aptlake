// 거래 목록 화면의 규칙 (React 무관): 조건 기본값, 전용면적 구간, 버전 이력 표시.
import type { TradeQuery } from "../api/endpoints";
import type { Available } from "../api/types";
import { lastDay } from "../lib/format";

/** 전용면적 구간 — 경계는 '미만'이라 상한을 소수로 둔다 (60 은 60~85 구간). */
export const AREA_BANDS: { key: string; label: string; min?: number; max?: number }[] = [
  { key: "", label: "전체" }, { key: "40", label: "40m² 미만", max: 39.9999 }, { key: "60", label: "40~60m²", min: 40, max: 59.9999 },
  { key: "85", label: "60~85m²", min: 60, max: 84.9999 }, { key: "135", label: "85~135m²", min: 85, max: 134.9999 },
  { key: "135p", label: "135m² 이상", min: 135 },
];
export const PAGE_SIZE = 100;

/** URL 조건 → 조회 조건. 기간 기본 = 기본 기준월 한 달, 해제 포함이 기본. 기준월을 아직 모르면 query=null. */
export function tradeFilter(params: URLSearchParams, avail: Available | null, fallbackSgg: string) {
  const sgg = params.get("sgg") ?? fallbackSgg;
  const base = avail?.default ?? "";
  const from = params.get("from") ?? (base ? `${base}-01` : "");
  const to = params.get("to") ?? (base ? lastDay(base) : "");
  const area = params.get("area") ?? "";
  const cancel = params.get("cancel") !== "0";
  const band = AREA_BANDS.find((a) => a.key === area) ?? AREA_BANDS[0];
  const query: TradeQuery | null = from && to
    ? { sgg, from, to, includeCancelled: cancel, limit: PAGE_SIZE, minArea: band.min, maxArea: band.max } : null;
  return { sgg, from, to, area, cancel, query };
}

/** 날짜 고르기 상한: 오늘과 조회 가능한 마지막 달 말일 중 이른 날 */
export function maxPickDate(avail: Available, today: string): string {
  return today < lastDay(avail.to) ? today : lastDay(avail.to);
}

export const VERSION_FIELD: Record<string, string> = {
  cancelled: "해제", cancelDate: "해제일", registeredDate: "등기일", aptDong: "동", dealKind: "거래유형", sellerType: "매도자", buyerType: "매수자",
};
/** 버전 이력의 값 표시 (예/아니오/없음) */
export const versionValue = (v: unknown) => (v === true ? "예" : v === false ? "아니오" : v == null || v === "" ? "없음" : String(v));
