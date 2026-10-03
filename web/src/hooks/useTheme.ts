import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
type Resolved = "light" | "dark";

const KEY = "aptlake.theme.choice";
/** 예전 키는 고르지 않아도 늘 "light" 를 저장했다 — "dark" 만 사용자가 고른 값으로 옮긴다. */
const OLD_KEY = "aptlake.theme";
const LABEL: Record<ThemeChoice, string> = { system: "OS 설정 따름", light: "밝게", dark: "어둡게" };

function osTheme(): Resolved {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
    const old = localStorage.getItem(OLD_KEY);
    if (old !== null) {
      localStorage.removeItem(OLD_KEY);
      if (old === "dark") { localStorage.setItem(KEY, "dark"); return "dark"; }
    }
  } catch { /* 저장소를 못 쓰면 OS 설정 */ }
  return "system";
}

/**
 * 버튼을 누를 때 다음 선택: 자동 → (지금과 반대로 고정) → (OS 와 같은 쪽으로 고정) → 자동.
 * 첫 누름은 늘 화면이 바뀌게 한다 (자동인데 '밝게 고정'으로 가면 눌러도 그대로라 고장처럼 보인다).
 */
export function nextChoice(choice: ThemeChoice, os: Resolved): ThemeChoice {
  const other: Resolved = os === "dark" ? "light" : "dark";
  return choice === "system" ? other : choice === other ? os : "system";
}

export function resolveTheme(choice: ThemeChoice): Resolved {
  return choice === "system" ? osTheme() : choice;
}

/** 첫 화면을 그리기 전에 테마를 정한다 (밝은 화면이 잠깐 보이지 않게). */
export function applyInitialTheme(): void {
  document.documentElement.dataset.theme = resolveTheme(readChoice());
}

/**
 * 화면 테마: 기본은 OS 설정(바뀌면 따라감), 직접 고르면 그 값을 이 브라우저에 기억.
 * 문서의 data-theme 은 늘 실제 적용된 값(light/dark) — 차트가 이 값의 변화를 보고 다시 그린다.
 */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  const [os, setOs] = useState<Resolved>(osTheme);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setOs(mq.matches ? "dark" : "light");
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const resolved: Resolved = choice === "system" ? os : choice;
  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
  }, [resolved]);
  const next = nextChoice(choice, os);
  const choose = (c: ThemeChoice) => {
    setChoice(c);
    try {
      if (c === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, c);
    } catch { /* 저장 못 해도 이번 화면에는 적용 */ }
  };
  return {
    choice, resolved, next, cycle: () => choose(next),
    label: `화면 테마: ${LABEL[choice]}${choice === "system" ? ` (지금 ${LABEL[resolved]})` : ""} — 누르면 ${LABEL[next]}`,
  };
}
