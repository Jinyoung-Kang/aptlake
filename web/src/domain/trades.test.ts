import { describe, expect, it } from "vitest";
import { maxPickDate, tradeFilter, versionValue } from "./trades";

const avail = { from: "2023-01", to: "2024-07", default: "2024-06" };

describe("거래 조건", () => {
  it("기본: 기본 기준월 한 달, 해제 포함, 면적 전체", () => {
    const f = tradeFilter(new URLSearchParams(), avail, "41135");
    expect(f.query).toEqual({ sgg: "41135", from: "2024-06-01", to: "2024-06-30", includeCancelled: true, limit: 100, minArea: undefined, maxArea: undefined });
  });
  it("면적 구간·해제 제외·지역은 URL 에서", () => {
    const f = tradeFilter(new URLSearchParams("sgg=11110&area=85&cancel=0&from=2024-01-01&to=2024-01-31"), avail, "41135");
    expect(f.query).toMatchObject({ sgg: "11110", includeCancelled: false, minArea: 60, maxArea: 84.9999 });
    expect(tradeFilter(new URLSearchParams("area=zzz"), avail, "x").query).toMatchObject({ minArea: undefined, maxArea: undefined });
  });
  it("기준월을 모르면 조회하지 않는다", () => {
    expect(tradeFilter(new URLSearchParams(), null, "41135").query).toBeNull();
  });
  it("날짜 상한은 오늘과 마지막 달 말일 중 이른 날, 버전 값 표시", () => {
    expect(maxPickDate(avail, "2026-10-01")).toBe("2024-07-31");
    expect(maxPickDate({ ...avail, to: "2026-10" }, "2026-10-01")).toBe("2026-10-01");
    expect([true, false, null, "", "2024-07-01"].map(versionValue)).toEqual(["예", "아니오", "없음", "없음", "2024-07-01"]);
  });
});
