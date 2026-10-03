// 색 대비 검사 (WCAG 2.2): theme.css 의 역할 토큰을 밝은·어두운 테마 각각에서 읽어 글자·조작 요소 대비를 확인한다.
//   글자 4.5:1 이상 (1.4.3), 입력·버튼 테두리와 초점 표시 3:1 이상 (1.4.11)
// 실패가 하나라도 있으면 종료 코드 1 — CI·make lint 에서 돈다. 사용: node scripts/contrast.mjs [--table]
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const css = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../src/theme.css"), "utf8");

function block(selector) {
  const i = css.indexOf(`${selector} {`);
  if (i < 0) throw new Error(`theme.css 에 ${selector} 블록이 없습니다`);
  const body = css.slice(i, css.indexOf("\n}", i));
  const out = {};
  for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

const light = block(":root");
const themes = { light, dark: { ...light, ...block(':root[data-theme="dark"]') } };

function hex(theme, name) {
  const v = theme[name];
  if (!v || !/^#[0-9a-f]{6}$/i.test(v)) throw new Error(`--${name} 는 #rrggbb 여야 대비를 잴 수 있습니다 (현재 ${v})`);
  return v;
}
function luminance(h) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const BACKGROUNDS = ["bg", "surface", "bg-sub", "bg-hover"];
const TEXT = ["text", "text-2", "text-3", "link", "accent", "up", "down", "flat", "good", "warn", "bad"];
// [글자, 바탕, 최소 대비, 쓰임]
const pairs = [
  ...TEXT.flatMap((t) => BACKGROUNDS.map((b) => [t, b, 4.5, "본문·표"])),
  ["accent", "accent-soft", 4.5, "선택된 칩·탭"],
  ["good", "good-soft", 4.5, "배지"], ["warn", "warn-soft", 4.5, "배지"], ["bad", "bad-soft", 4.5, "배지"],
  ["on-accent", "accent-fill", 4.5, "주 버튼"], ["on-accent", "danger-fill", 4.5, "위험 버튼"],
  ...TEXT.map((t) => [t, "selected", 4.5, "선택된 행·키보드 초점 행"]),
  // 스위치: 손잡이와 판(켜짐·꺼짐), 판과 둘레 바탕 (1.4.11 — 상태를 알아보는 데 필요한 부분)
  ["switch-knob", "switch-off", 3, "스위치 손잡이"], ["switch-knob", "switch-on", 3, "스위치 손잡이"],
  ...["bg", "surface", "bg-sub"].flatMap((b) => [["switch-off", b, 3, "스위치 판"], ["switch-on", b, 3, "스위치 판"]]),
  ...["bg", "surface", "bg-sub"].map((b) => ["border-strong", b, 3, "입력·버튼 테두리"]),
  ...["bg", "surface", "bg-sub"].map((b) => ["focus", b, 3, "초점 표시"]),
];

let failed = 0;
const rows = [];
for (const [name, theme] of Object.entries(themes)) {
  for (const [fg, bg, min, use] of pairs) {
    const r = ratio(hex(theme, fg), hex(theme, bg));
    const ok = r >= min;
    if (!ok) failed++;
    rows.push({ theme: name, fg, bg, ratio: r.toFixed(2), min, ok, use });
  }
}
const show = process.argv.includes("--table") ? rows : rows.filter((r) => !r.ok);
if (show.length) console.table(show);
const worst = (t) => Math.min(...rows.filter((r) => r.theme === t).map((r) => Number(r.ratio) / r.min));
console.log(`대비 검사: ${rows.length}쌍 중 실패 ${failed} · 여유(최소 대비 대비 배수) 밝게 ${worst("light").toFixed(2)} · 어둡게 ${worst("dark").toFixed(2)}`);
process.exit(failed ? 1 : 0);
