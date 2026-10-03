import { describe, expect, it } from "vitest";
import type { ComplexDetail } from "../api/types";
import { bandOf, inBand, presentBands, validMedian } from "./complex";
import { complexSearchTerm } from "./search";

// [계약일, 전용면적, 층, 금액, m²당, 해제, 이상치]
const H: ComplexDetail["history"] = [
  ["2024-01-02", 59.9, 3, 50000, 800, 0, 0], ["2024-02-03", 84.9, 10, 90000, 1000, 0, 0],
  ["2024-03-04", 84.9, 11, 95000, 1100, 1, 0], ["2024-04-05", 84.9, 12, 200000, 2400, 0, 1], ["2024-05-06", 60, 5, 60000, 1200, 0, 0],
];

describe("단지 거래 이력", () => {
  it("면적 구간: lo 이상 hi 미만, 거래 있는 구간만 고를 수 있음", () => {
    expect(inBand(H, bandOf("s")).map((h) => h[1])).toEqual([59.9]);
    expect(inBand(H, bandOf("m")).map((h) => h[1])).toEqual([84.9, 84.9, 84.9, 60]);
    expect(presentBands(H).map((b) => b.key)).toEqual(["all", "s", "m"]);
    expect(bandOf("nope").key).toBe("all");
  });
  it("중위가는 해제·이상치를 뺀 거래로", () => {
    expect(validMedian(inBand(H, bandOf("m")))).toEqual({ median: 1100, n: 2 });
    expect(validMedian([])).toEqual({ median: null, n: 0 });
  });
});

describe("상단 검색", () => {
  it("단지 검색은 2글자 이상, 초성만이면 보내지 않는다", () => {
    expect(["래", " ㅂㄷ ", "ㄹㅁ ㅇ", "분당", " 래미안 "].map(complexSearchTerm)).toEqual([null, null, null, "분당", "래미안"]);
  });
});
