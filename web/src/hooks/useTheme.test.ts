// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyInitialTheme, nextChoice, readChoice, resolveTheme } from "./useTheme";

function osDark(dark: boolean) {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: dark && q.includes("dark"), addEventListener() {}, removeEventListener() {} }));
}
beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("화면 테마", () => {
  it("고른 적 없으면 OS 설정을 따른다", () => {
    osDark(true);
    expect(readChoice()).toBe("system");
    applyInitialTheme();
    expect(document.documentElement.dataset.theme).toBe("dark");
    osDark(false);
    expect(resolveTheme("system")).toBe("light");
  });
  it("직접 고른 값이 OS 설정보다 먼저", () => {
    osDark(true);
    localStorage.setItem("aptlake.theme.choice", "light");
    expect(resolveTheme(readChoice())).toBe("light");
  });
  it("예전 키: 늘 저장되던 'light' 는 버리고(OS 설정), 사용자가 고른 'dark' 만 옮긴다", () => {
    localStorage.setItem("aptlake.theme", "light");
    expect(readChoice()).toBe("system");
    expect(localStorage.getItem("aptlake.theme")).toBeNull();
    localStorage.setItem("aptlake.theme", "dark");
    expect(readChoice()).toBe("dark");
    expect(localStorage.getItem("aptlake.theme.choice")).toBe("dark");
  });
  it("누를 때마다: 자동 → 반대로 고정 → OS 와 같게 고정 → 자동 (첫 누름은 늘 화면이 바뀜)", () => {
    expect([nextChoice("system", "light"), nextChoice("dark", "light"), nextChoice("light", "light")]).toEqual(["dark", "light", "system"]);
    expect(nextChoice("system", "dark")).toBe("light");
  });
});
