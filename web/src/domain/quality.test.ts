import { describe, expect, it } from "vitest";
import { completion, gridCells, parseCell, rollupCells, rollupMonths } from "./quality";

describe("데이터 품질", () => {
  it("완결도 = 반영 완료 ÷ 전체 파티션", () => {
    expect(completion({ MERGED: 3, PENDING: 1 })).toEqual({ total: 4, merged: 3, pct: 75 });
    expect(completion({})).toEqual({ total: 0, merged: 0, pct: 0 });
  });
  it("히트맵 칸: 시도별 반영 비율·상태 문구, 시군구별 상태 순번", () => {
    const rollup = { cells: { "41": { "202407": { MERGED: 3, RETRY: 1 } }, "11": { "202406": { PENDING: 2 } } } };
    const months = rollupMonths(rollup);
    expect(months).toEqual(["202406", "202407"]);
    expect(rollupCells(rollup, ["11", "41"], months)).toEqual([[0, 0, 0, "미수집 2"], [1, 1, 75, "반영 완료 3 · 재시도 대기 1"]]);
    const grid = { cells: { "41135": { "202407": ["QUARANTINED", null] as [string, null] } } };
    expect(gridCells(grid, ["41131", "41135"], months)).toEqual([[1, 1, 5, null]]);
  });
  it("선택 칸 'sgg:YYYYMM' 해석 — 모르는 시군구·형식 오류는 무시", () => {
    const known = (c: string) => c === "41135";
    expect(parseCell("41135:202408", known)).toEqual(["41135", "2024-08"]);
    expect(parseCell("99999:202408", known)).toBeNull();
    expect(parseCell("41135", known)).toBeNull();
    expect(parseCell(null, known)).toBeNull();
  });
});
