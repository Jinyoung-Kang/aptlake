import { describe, expect, it } from "vitest";
import type { SggSummary } from "../api/types";
import { mapPieces, metricOf, metricValue, quantileBins } from "./market";

const sgg = (o: Partial<SggSummary>): SggSummary => ({
  sggCd: "11110", trades: 10, cancelled: 0, cancelRate: 0, median: 1500, sample: 10, lowSample: false,
  medianYoY: 2.5, tradesYoY: null, tradesMoM: null, ...o,
});
const colors = { div: ["d0", "d1", "d2", "d3", "d4"], seq: ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"] };

describe("시장 지도", () => {
  it("지표 값: 표본 부족 중위가·신고 0건 해제율은 자료 없음", () => {
    expect(metricValue(sgg({ lowSample: true }), "median")).toBeNull();
    expect(metricValue(sgg({ trades: 0, cancelled: 0, cancelRate: null }), "cancelRate")).toBeNull();
    expect(metricValue(sgg({ trades: 0, cancelled: 2, cancelRate: 100 }), "cancelRate")).toBe(100);
    expect(metricValue(undefined, "trades")).toBeNull();
    expect(metricOf(null)).toBe("median");
  });
  it("분위 경계: 같은 값은 하나로, 값이 적으면 경계 없음", () => {
    expect(quantileBins([1, 2, 3, 4, 5, 6], 6)).toEqual([2, 3, 4, 5, 6]);
    expect(quantileBins([5, 5, 5, 5, 5, 5, 1], 6)).toEqual([5]);
    expect(quantileBins([1, 2], 6)).toEqual([]);
  });
  it("변화율은 고정 5구간(0 근처 중립색), 나머지는 분위 구간 + 양 끝 열린 구간", () => {
    const yoy = mapPieces([], "medianYoY", colors);
    expect(yoy.map((p) => p.color)).toEqual(colors.div);
    expect(yoy[2]).toEqual({ gte: -1, lte: 1, label: "−1 ~ +1%", color: "d2" });
    const tr = mapPieces([10, 20, 30, 40, 50, 60], "trades", colors);
    expect(tr[0]).toEqual({ lt: 20, label: "20 미만", color: "s1" });
    expect(tr.at(-1)).toEqual({ gte: 60, label: "60 이상", color: "s7" });
  });
});
