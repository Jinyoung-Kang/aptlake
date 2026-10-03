import { useEffect, useState } from "react";

const THEME_KEY = "aptlake.theme";

/** 화면 테마 (밝게·어둡게) — 고른 값은 이 브라우저에 기억한다. */
export function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem(THEME_KEY) || "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* 저장 못 해도 이번 화면에는 적용 */ }
  }, [theme]);
  return [theme, setTheme];
}
