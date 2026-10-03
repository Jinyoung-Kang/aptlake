import { describe, expect, it } from "vitest";
import {
  DASH, duration, esc, initials, isoDate, kst, kstShort, lastDay, manwon, monthRange, num, pct, quantile, relative,
  ymAdd, ymDiff, ymLabel, ymOf,
} from "./format";

describe("숫자 표기", () => {
  it("천 단위 구분과 자릿수", () => {
    expect(num(1234567)).toBe("1,234,567");
    expect(num(1234.5, 1)).toBe("1,234.5");
    expect(num(2, 2)).toBe("2.00");
    expect(num(null)).toBe(DASH);
    expect(num(Number.NaN)).toBe(DASH);
  });
  it("만원 → 억·만", () => {
    expect(manwon(195200)).toBe("19억 5,200만");
    expect(manwon(100000)).toBe("10억");
    expect(manwon(8500)).toBe("8,500만");
    expect(manwon(null)).toBe(DASH);
  });
  it("변화율은 양수에 + 를 붙인다", () => {
    expect(pct(3.14159)).toBe("+3.1%");
    expect(pct(-0.04, 2)).toBe("-0.04%");
    expect(pct(0)).toBe("0.0%");
    expect(pct(undefined)).toBe(DASH);
  });
});

describe("시각 (KST)", () => {
  it("UTC 를 한국 시각으로", () => {
    expect(kst("2026-09-28T12:59:17Z")).toBe("2026-09-28 21:59:17");
    expect(kst("2026-09-28T15:00:00Z", false)).toBe("2026-09-29 00:00");
    expect(kstShort("2026-09-28T15:05:00Z")).toBe("09-29 00:05");
    expect(kst(null)).toBe(DASH);
  });
  it("소요 시간", () => {
    expect(duration(42)).toBe("42초");
    expect(duration(125)).toBe("2분 5초");
    expect(duration(120)).toBe("2분");
    expect(duration(3 * 3600 + 120)).toBe("3시간 2분");
    expect(duration(-1)).toBe(DASH);
  });
  it("상대 시각", () => {
    const now = Date.parse("2026-10-01T00:00:00Z");
    expect(relative("2026-09-30T23:59:30Z", now)).toBe("방금");
    expect(relative("2026-09-30T23:50:00Z", now)).toBe("10분 전");
    expect(relative("2026-09-30T21:00:00Z", now)).toBe("3시간 전");
    expect(relative("2026-09-28T00:00:00Z", now)).toBe("3일 전");
    expect(relative("2026-10-01T00:00:30Z", now)).toBe("곧");
    expect(relative("2026-10-01T02:00:00Z", now)).toBe("2시간 후");
  });
});

describe("월 연산", () => {
  it("더하기·차이·범위", () => {
    expect(ymAdd("2024-11", 3)).toBe("2025-02");
    expect(ymAdd("2024-01", -1)).toBe("2023-12");
    expect(ymDiff("2023-11", "2024-02")).toBe(3);
    expect(monthRange("2024-11", "2025-01")).toEqual(["2024-11", "2024-12", "2025-01"]);
    expect(ymLabel("2024-07")).toBe("2024.07");
    expect(ymLabel(null)).toBe(DASH);
    expect(ymOf(new Date(2024, 6, 15))).toBe("2024-07");
  });
  it("말일·날짜", () => {
    expect(lastDay("2024-02")).toBe("2024-02-29");
    expect(lastDay("2023-02")).toBe("2023-02-28");
    expect(isoDate(new Date(2024, 0, 5))).toBe("2024-01-05");
  });
});

describe("검색·통계·이스케이프", () => {
  it("한글 초성", () => {
    expect(initials("분당구")).toBe("ㅂㄷㄱ");
    expect(initials("A1 분당")).toBe("A1 ㅂㄷ");
  });
  it("선형 보간 분위수 (numpy 기본값과 같은 정의)", () => {
    expect(quantile([1, 2, 3, 4], 0.5)).toBe(2.5);
    expect(quantile([10], 0.25)).toBe(10);
    expect(quantile([], 0.5)).toBeNull();
  });
  it("툴팁 HTML 에 들어가는 문자열은 이스케이프", () => {
    expect(esc(`<img src=x onerror="a('b')">&`)).toBe("&lt;img src=x onerror=&quot;a(&#39;b&#39;)&quot;&gt;&amp;");
    expect(esc(null)).toBe("");
  });
});
