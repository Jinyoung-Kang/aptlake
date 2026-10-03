// 키보드만으로 위젯 조작: 상단 검색(↓·Enter), 지역 선택 팝업(Enter 열기·Esc 닫기 뒤 초점·Tab 으로 고르기), 이전 달 버튼.
//   QA_TOOLS=<puppeteer-core 설치 폴더> node qa/probes/ui_keyboard.mjs   (QA 스택 웹 3710)
import { createRequire } from "node:module";
const req = createRequire(`${process.env.QA_TOOLS}/package.json`);
const puppeteer = req("puppeteer-core");
const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const state = () => page.evaluate(() => ({ hash: location.hash, active: `${document.activeElement?.tagName}.${document.activeElement?.className}`.slice(0, 50),
  popover: !!document.querySelector(".popover, [role=listbox]:not([hidden])"), expanded: document.activeElement?.getAttribute("aria-expanded") }));
await page.goto("http://127.0.0.1:3710/#/market", { waitUntil: "networkidle0" });
// 1) 상단 검색: Tab 으로 들어가 입력 → ↓ → Enter 로 이동
await page.focus('input[role="combobox"]');
await page.keyboard.type("QA1단지"); await sleep(800);
const opts = await page.$$eval('[role="option"]', (o) => o.map((x) => x.textContent.trim().slice(0, 30)));
await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter"); await sleep(1200);
console.log("검색 → ↓ → Enter:", { 후보: opts.slice(0, 3), 이동한_주소: (await state()).hash });
// 2) 지역 선택 팝업: 버튼에 초점 → Enter 로 열기 → Esc 로 닫기 → 초점이 버튼으로 돌아오는가
await page.goto("http://127.0.0.1:3710/#/trades?sgg=11110", { waitUntil: "networkidle0" }); await page.waitForSelector(".region-picker > .btn"); await sleep(500);
await page.focus(".region-picker > .btn"); await page.keyboard.press("Enter"); await sleep(400);
const opened = await state();
await page.keyboard.type("부산"); await sleep(400);
await page.keyboard.press("Escape"); await sleep(300);
console.log("지역 선택 Enter 로 열림:", opened.popover, opened.expanded, "· Esc 뒤 팝업:", (await state()).popover, "· 초점:", (await state()).active);
// 3) 지역 선택에서 키보드로 고르기: 열기 → 입력 → Tab 으로 후보 → Enter
await page.focus(".region-picker > .btn"); await page.keyboard.press("Enter"); await sleep(300);
await page.keyboard.type("QA2구"); await sleep(400);
for (let i = 0; i < 3; i++) { await page.keyboard.press("Tab"); }
const focusedOpt = await page.evaluate(() => document.activeElement?.textContent?.trim().slice(0, 30));
await page.keyboard.press("Enter"); await sleep(1200);
console.log("지역 선택 Tab×3 → 초점:", focusedOpt, "→ Enter 뒤 주소:", (await state()).hash);
// 4) 월 선택(지역 분석): ◀ 버튼·월 팝업 Enter/Esc
await page.goto("http://127.0.0.1:3710/#/region/11110?tab=distribution", { waitUntil: "networkidle0" }); await page.waitForSelector("button[aria-label=\"이전 달\"]"); await sleep(500);
const before = (await state()).hash;
const prev = await page.$('button[aria-label="이전 달"]');
await prev.focus(); await page.keyboard.press("Enter"); await sleep(800);
console.log("이전 달 Enter:", before, "→", (await state()).hash);
await browser.close();
