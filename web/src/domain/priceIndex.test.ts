import { describe, expect, it } from "vitest";
import type { IndexSummaryItem, RegionIndex } from "../api/types";
import { corrText, rebased, representative } from "./priceIndex";

const idx = (series: [string, number][], ref: [string, number][]): RegionIndex => ({
  regionId: "00", method: "HEDONIC_TD_v1", base: "2023-01=100", disclaimer: "",
  series: series.map(([period, value]) => ({ period, value, ciLow: value - 1, ciHigh: value + 1, nObs: 10 })),
  reference: { source: "R-ONE", series: ref.map(([period, value]) => ({ period, value })) },
  validation: null,
});

describe("가격지수", () => {
  it("표시 구간에서 R-ONE 과 겹치는 첫 달을 둘 다 100 으로", () => {
    const v = rebased(idx([["2024-01", 110], ["2024-02", 121], ["2024-03", 132]], [["2024-02", 50], ["2024-03", 55]]), "all");
    expect(v.base).toBe("2024-02");
    expect(v.rows.map((r) => [r.period, +r.v.toFixed(1), r.ref])).toEqual([["2024-01", 90.9, null], ["2024-02", 100, 100], ["2024-03", 109.1, 110]]);
    expect(rebased(idx([["2024-01", 110], ["2024-02", 121]], []), "12").base).toBeNull();  // 겹치는 달 없음 → 그대로
    expect(rebased(idx([["2024-01", 1], ["2024-02", 2], ["2024-03", 3]], []), "12").rows).toHaveLength(3);
  });
  it("대표값은 확정 달, 상관 해석 문구", () => {
    const p = { period: "2024-03", value: 1, provisional: true, mom: null, yoy: null };
    const item = { ...p, regionId: "00", spark: [], corrMoM: null, directionMatch: null, months: 0, confirmed: { ...p, period: "2024-02", provisional: false } } as IndexSummaryItem;
    expect(representative(item).period).toBe("2024-02");
    expect(representative({ ...item, confirmed: null }).period).toBe("2024-03");
    expect([null, 0.85, 0.6, 0.3, 0.1].map(corrText)).toEqual(["비교 기간 부족", "매우 비슷하게 움직임", "대체로 비슷함", "약하게 비슷함", "차이가 큼"]);
  });
});
