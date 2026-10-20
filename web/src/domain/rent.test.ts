import { describe, expect, it } from "vitest";
import type { JeonseRatioRow, RentMonthRow } from "../api/types";
import { fillRentMonths, latestRatio, rentFilter, rentPrice, rentStats } from "./rent";

const row = (dealYm: string, o: Partial<RentMonthRow> = {}): RentMonthRow => ({
  dealYm, contracts: 10, jeonse: 6, wolse: 4, renewals: 3, newContracts: 5, typeUnknown: 2, renewalRightUsed: 1, jeonseSample: 6, outliers: 0,
  jeonseP25PerM2: 600, jeonseMedianPerM2: 700, jeonseP75PerM2: 800, wolseMedianRent: 150, wolseMedianDeposit: 5000, ...o,
});

describe("전월세 규칙 (ADR-050)", () => {
  it("빈 달은 0건으로 채운다", () => {
    const rows = fillRentMonths([row("2024-02")], "2024-01", "2024-03");
    expect(rows.map((r) => [r.dealYm, r.contracts])).toEqual([["2024-01", 0], ["2024-02", 10], ["2024-03", 0]]);
  });

  it("갱신 비율은 '미상'을 분모에서 뺀다 — 원천이 비운 계약 구분을 신규로 세지 않는다", () => {
    const st = rentStats([row("2024-01"), row("2024-02", { provisional: true, jeonseMedianPerM2: 900 })]);
    expect(st.contracts).toBe(20);
    expect(st.jeonseShare).toBeCloseTo(60);
    expect(st.renewalShare).toBeCloseTo((6 / 16) * 100);  // 갱신 6 ÷ (갱신 6 + 신규 10), 미상 4 제외
    expect(st.typeUnknownShare).toBeCloseTo(20);
    expect(st.lastConfirmed?.dealYm).toBe("2024-01");  // 잠정 달이 아닌 최근 달
    expect(rentStats([]).renewalShare).toBeNull();
  });

  it("대표 전세가율은 값이 있는 확정 달", () => {
    const r = (dealYm: string, ratioPct: number | null, provisional?: boolean): JeonseRatioRow =>
      ({ dealYm, windowFrom: dealYm, tradeMedianPerM2: 1000, tradeSample: 9, jeonseMedianPerM2: 700, jeonseSample: 9, ratioPct, provisional });
    expect(latestRatio([r("2024-05", 70), r("2024-06", null), r("2024-07", 72, true)])?.dealYm).toBe("2024-05");
    expect(latestRatio([r("2024-07", 72, true)])?.dealYm).toBe("2024-07");
    expect(latestRatio([r("2024-07", null)])).toBeUndefined();
  });

  it("목록 조건: 종류·면적·기본 기간", () => {
    const avail = { from: "2021-01", to: "2026-10", default: "2026-08" };
    const f = rentFilter(new URLSearchParams("sgg=11110&kind=jeonse&area=85"), avail, "41135");
    expect(f.query).toEqual({ sgg: "11110", from: "2026-08-01", to: "2026-08-31", limit: 100, kind: "jeonse", minArea: 60, maxArea: 84.9999 });
    expect(rentFilter(new URLSearchParams("kind=x"), avail, "41135").kind).toBe("");
    expect(rentFilter(new URLSearchParams(""), null, "41135").query).toBeNull();
    expect(rentPrice("wolse", "5,000만", "150만")).toBe("5,000만 / 월 150만");
  });
});
