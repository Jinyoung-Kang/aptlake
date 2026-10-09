import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { planRow, todayPct } from "./developer";

const pro = { planId: "pro", rpm: 600, dailyRows: 2000000, maxRangeMonths: null, maxPageSize: 1000, bulk: true };

describe("개발자 화면 (ADR-047)", () => {
  it("플랜 표 문구는 응답 값에서", () => {
    expect(planRow(pro)).toEqual({ p: "pro", who: "발급 키", rpm: 600, rows: 2000000, range: "전체", page: 1000, bulk: "내보내기·피드" });
    expect(planRow({ ...pro, planId: "free", maxRangeMonths: 24, bulk: false }).range).toBe("24개월");
  });
  it("오늘 행 사용률은 0~100", () => {
    expect(todayPct({ limits: { ...pro, dailyRows: 50000 }, today: { rowsUsed: 12500, rowsLeft: 37500 } })).toBe(25);
    expect(todayPct({ limits: { ...pro, dailyRows: 50000 }, today: { rowsUsed: 60000, rowsLeft: 0 } })).toBe(100);
  });
});

it("개발자 화면에 플랜 한도 숫자를 코드로 박아 두지 않는다 (값의 출처는 GET /v1/plans)", () => {
  const src = readFileSync(resolve(__dirname, "../pages/DeveloperPage.tsx"), "utf8");
  for (const n of ["5000", "50000", "2000000", "200000"]) expect(src).not.toMatch(new RegExp(`\\b${n}\\b`));
});
