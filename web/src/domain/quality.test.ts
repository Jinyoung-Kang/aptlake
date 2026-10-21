import { describe, expect, it } from "vitest";
import { completion, gridCells, latencyBars, latencyExcluded, latencyValue, latencyWindow, parseCell, rollupCells, rollupMonths, schemaVersionLabel, schemaVersionText } from "./quality";

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
  it("원천 스키마 판 이름: 빈 응답·판 기록 전·vN", () => {
    expect(["empty", "unrecorded", "1", "2"].map(schemaVersionLabel)).toEqual(["빈 응답", "판 기록 전", "v1", "v2"]);
    expect(schemaVersionText({ "2": 1200, "1": 30, empty: 4 })).toBe("v1 30 · v2 1,200 · 빈 응답 4");
  });
  it("선택 칸 'sgg:YYYYMM' 해석 — 모르는 시군구·형식 오류는 무시", () => {
    const known = (c: string) => c === "41135";
    expect(parseCell("41135:202408", known)).toEqual(["41135", "2024-08"]);
    expect(parseCell("99999:202408", known)).toBeNull();
    expect(parseCell("41135", known)).toBeNull();
    expect(parseCell(null, known)).toBeNull();
  });
});

describe("원천 반영 지연 (ADR-046)", () => {
  const day = { unit: "day" as const, n: 17, p50: 6, p90: 25, p99: 45, distribution: [[1, 2], [3, 5], [6, 4], [12, 3], [25, 2], [45, 1]] as [number, number][],
                excluded: { initial_load: 250, low_resolution: 1 }, measuredSince: "2026-09-28" };
  it("일 분포를 넓어지는 구간으로 묶는다 (합계 보존)", () => {
    const bars = latencyBars(day);
    expect(bars.map(([, n]) => n)).toEqual([0, 2, 5, 4, 3, 2, 1, 0, 0]);
    expect(bars[2][0]).toBe("2–3일");
  });
  it("분 구간 이름과 표본 없을 때 숫자를 숨김", () => {
    const min = { unit: "minute" as const, n: 0, p50: null, p90: null, p99: null, excluded: {}, measuredSince: null,
                  distribution: [[15, 0], [60, 0], [1440, 0], [null, 0]] as [number | null, number][] };
    expect(latencyBars(min).map(([l]) => l)).toEqual(["15분 이하", "1시간 이하", "1일 이하", "1일 초과"]);
    expect(latencyValue(min, null)).toBe("측정 중");
    expect(latencyValue({ ...min, n: 3 }, 90)).toBe("1.5시간");
    expect(latencyValue(day, 6)).toBe("6일");
  });
  it("제외 사유는 많은 순·이름으로", () => {
    expect(latencyExcluded(day)[0]).toEqual(["첫 수집(초기 적재)에 이미 있음", 250]);
  });
  it("최근 30일 창은 KST 날짜", () => {
    expect(latencyWindow(new Date("2026-10-09T16:00:00Z"))).toEqual({ from: "2026-09-11", to: "2026-10-10" });
  });
});
