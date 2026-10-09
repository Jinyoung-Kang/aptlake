// 개발자 화면의 규칙 (React·차트 무관): 플랜 표 문구, 오늘 행 사용률. 숫자는 모두 API 응답에서 온다 (ADR-047).
import type { MyUsage, PlanView } from "../api/types";

export const PLAN_WHO: Record<string, string> = { anonymous: "키 없음 · IP 당", free: "발급 키", pro: "발급 키" };

/** 플랜 표의 한 줄 (표시 문구) */
export function planRow(p: PlanView) {
  return {
    p: p.planId,
    who: PLAN_WHO[p.planId] ?? "",
    rpm: p.rpm,
    rows: p.dailyRows,
    range: p.maxRangeMonths == null ? "전체" : `${p.maxRangeMonths}개월`,
    page: p.maxPageSize,
    bulk: p.bulk ? "내보내기·피드" : "×",
  };
}

/** 이 키의 오늘 행 사용률 (0~100) */
export function todayPct(u: Pick<MyUsage, "today" | "limits">): number {
  return u.limits.dailyRows ? Math.min(100, (u.today.rowsUsed / u.limits.dailyRows) * 100) : 0;
}
