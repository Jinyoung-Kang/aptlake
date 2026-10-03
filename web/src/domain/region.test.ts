import { describe, expect, it } from "vitest";
import type { MonthRow } from "../api/types";
import { monthRange } from "../lib/format";
import { fillMonths, latestQuote, medianYoY, recentStats, regionPeriod, splitPoints, WEB_MAX_MONTHS } from "./region";

const row = (dealYm: string, o: Partial<MonthRow> = {}): MonthRow => ({
  dealYm, reported: 10, trades: 9, cancelled: 1, sampleSize: 9, outliers: 0,
  p25PricePerM2: 900, medianPricePerM2: 1000, p75PricePerM2: 1100, ...o,
});
const avail = { from: "2023-01", to: "2024-07", default: "2024-05" };

describe("지역 분석 기간", () => {
  it("기본 = 최근 24개월(조회 범위 안으로 자름), 전년비용으로 12개월 앞부터 받음", () => {
    expect(regionPeriod(new URLSearchParams(), avail)).toEqual(
      { min: "2023-01", max: "2024-07", from: "2023-01", to: "2024-07", ym: "2024-05", fetchFrom: "2023-01" });
    const p = regionPeriod(new URLSearchParams("from=2024-03&to=2024-06&ym=2024-04"), avail);
    expect([p.from, p.to, p.ym, p.fetchFrom]).toEqual(["2024-03", "2024-06", "2024-04", "2023-03"]);
  });
  it("조회 범위를 아직 모르면 빈 값 (요청하지 않음)", () => {
    expect(regionPeriod(new URLSearchParams(), null)).toMatchObject({ from: "", to: "", fetchFrom: "" });
  });
});

describe("월별 지표", () => {
  it("빈 달은 0건 행으로 채운다", () => {
    const out = fillMonths([row("2024-01"), row("2024-03")], "2024-01", "2024-03");
    expect(out.map((r) => [r.dealYm, r.trades, r.medianPricePerM2])).toEqual([["2024-01", 9, 1000], ["2024-02", 0, null], ["2024-03", 9, 1000]]);
  });
  it("대표 시세 = 표본 충분·확정 최근 달, 전년비는 두 달 모두 값이 있을 때만", () => {
    const full = [row("2023-05", { medianPricePerM2: 800 }), row("2024-05"), row("2024-06", { provisional: true, medianPricePerM2: 1200 }),
                  row("2024-07", { lowSample: true })];
    expect(latestQuote(full)).toEqual({ cur: full[1], yoy: 25 });
    expect(latestQuote([row("2024-06", { provisional: true })])?.cur.dealYm).toBe("2024-06");  // 확정 달이 없으면 최근 달
    expect(latestQuote([row("2024-06", { medianPricePerM2: null })])).toBeNull();
    expect(medianYoY(row("2024-05"), undefined)).toBeNull();
  });
  it("최근 12개월: 거래 합·해제율·중위가 범위·최근 확정 월", () => {
    const rows = [row("2024-01", { medianPricePerM2: 900 }), row("2024-02", { trades: 0, cancelled: 0, medianPricePerM2: null }),
                  row("2024-03", { provisional: true, medianPricePerM2: 1300 })];
    const st = recentStats(rows);
    expect(st.trades).toBe(18);
    expect(st.cancelRate).toBeCloseTo(10);
    expect(st.medianRange).toEqual({ lo: 900, hi: 1300, n: 2 });
    expect(st.lastConfirmed?.dealYm).toBe("2024-01");
    expect(recentStats([row("2024-01", { trades: 0, cancelled: 0, medianPricePerM2: null })])).toMatchObject({ cancelRate: null, medianRange: null });
  });
  it("산점도 점: 해제가 이상치보다 먼저 (해제된 이상치는 해제로)", () => {
    const pts: [number, number, number | null, number, number][] = [[84, 1e5, 5, 0, 0], [59, 9e4, null, 1, 1], [120, 3e5, 20, 0, 1]];
    expect(splitPoints(pts)).toEqual({ normal: [[84, 1e5, 5]], cancelled: [[59, 9e4, null]], outlier: [[120, 3e5, 20]] });
  });
});

describe("QA-015 실제로 받는 기간이 웹 플랜 상한(60개월)을 넘지 않는다", () => {
  // 전년 대비 계산용으로 12개월을 더 당겨 받는데, 고른 기간이 60개월이면 받는 기간이 72개월이 되어 422 였다 ('전체' 버튼)
  const avail = { from: "2021-01", to: "2026-09", default: "2026-07" } as Parameters<typeof regionPeriod>[1];
  it.each([
    ["전체(60개월로 잘린 범위)", "from=2021-10&to=2026-09"],
    ["4년 11개월", "from=2021-11&to=2026-09"],
    ["4년 6개월", "from=2022-04&to=2026-09"],
  ])("%s", (_name, q) => {
    const { fetchFrom, to } = regionPeriod(new URLSearchParams(q), avail);
    expect(monthRange(fetchFrom, to).length).toBeLessThanOrEqual(WEB_MAX_MONTHS);
  });
  it("여유가 있으면 전년 대비용 12개월을 그대로 당겨 받는다", () => {
    expect(regionPeriod(new URLSearchParams("from=2024-01&to=2024-12"), avail).fetchFrom).toBe("2023-01");
  });
});
