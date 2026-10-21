import { describe, expect, it } from "vitest";
import type { JeonseRatioRow, RentMonthRow } from "../api/types";
import { cellValue, fillRentMonths, latestRatio, rentAmount, rentFilter, rentPrice, rentStats, rentVersionValue } from "./rent";

const row = (dealYm: string, o: Partial<RentMonthRow> = {}): RentMonthRow => ({
  dealYm, contracts: 10, jeonse: 6, wolse: 4, renewals: 3, newContracts: 5, typeUnknown: 2, renewalRightUsed: 1, jeonseSample: 6, outliers: 0,
  jeonseP25PerM2: 600, jeonseMedianPerM2: 700, jeonseP75PerM2: 800, wolseMedianRent: 150, wolseMedianDeposit: 5000, ...o,
});

describe("전월세 규칙 (ADR-050)", () => {
  it("빈 달은 0건으로 채운다", () => {
    const rows = fillRentMonths([row("2024-02")], "2024-01", "2024-03");
    expect(rows.map((r) => [r.dealYm, r.contracts])).toEqual([["2024-01", 0], ["2024-02", 10], ["2024-03", 0]]);
  });

  it("발행된 적이 없는 달(행 없음)은 0건이 아니라 빈칸 — 그 밖에 응답에 없는 달만 0건", () => {
    const rows = fillRentMonths([row("2024-02"), row("2024-04")], "2024-01", "2024-04", ["2024-03", "2024-04", "2023-12"]);
    expect(rows.map((r) => [r.dealYm, !!r.uncollected])).toEqual([["2024-01", false], ["2024-02", false], ["2024-03", true], ["2024-04", false]]);
    // 목록에 있어도 행이 있는 달(2024-04)은 발행된 값 그대로 — 완결은 '발행된 적이 있는가'라 행이 있으면 발행된 달이다
    expect(rows.map((r) => cellValue(r, r.jeonse))).toEqual([0, 6, null, 6]);
    expect(rows[3]).toEqual(row("2024-04"));
    // 최근 12개월 합계에서 빠진 달 수를 같이 알린다
    expect(rentStats(rows)).toMatchObject({ contracts: 20, uncollected: 1 });
  });

  it("최근 확정 월은 잠정이 아닌 최근 달 — 발행 전 목록에 있어도 행이 있으면 후보", () => {
    const rows = fillRentMonths(
      [row("2024-10", { jeonseMedianPerM2: 710 }), row("2024-11", { jeonseMedianPerM2: 711 }), row("2024-12", { provisional: true, jeonseMedianPerM2: 712 })],
      "2024-10", "2024-12", ["2024-11"]);
    expect(rentStats(rows).lastConfirmed?.dealYm).toBe("2024-11");
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
      ({ dealYm, windowFrom: dealYm, tradeMedianPerM2: 1000, tradeSample: 9, jeonseMedianPerM2: 700, jeonseSample: 9, ratioPct, provisional, complete: ratioPct != null });
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

  it("버전 이력 값: 원천이 비운 값(null·빈 문자열)은 '미상' — 매매 이력의 '없음'과 다르다", () => {
    expect([null, undefined, "", "갱신", 55000, -19870].map(rentVersionValue)).toEqual(["미상", "미상", "미상", "갱신", "55000", "-19870"]);
  });

  it("상세 금액: 비어 있으면 '미상', 0·음수는 값 그대로", () => {
    expect([null, undefined, 0, 55000, -19870].map(rentAmount)).toEqual(["미상", "미상", "0만", "5억 5,000만", "-1억 9,870만"]);
  });
});
