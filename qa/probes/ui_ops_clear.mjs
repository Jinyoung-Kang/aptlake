// QA-001 화면 흐름: 수집 상태의 '로그 비우기'·'되돌리기'가 운영자 키를 요구하는가 (QA 스택 웹, 키는 qa/.env.keys 에서 읽고 출력하지 않음)
//   QA_TOOLS=<puppeteer-core 설치 폴더> node qa/probes/ui_ops_clear.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const req = createRequire(`${process.env.QA_TOOLS}/package.json`);
const puppeteer = req("puppeteer-core");
const keys = Object.fromEntries(readFileSync("qa/.env.keys", "utf8").trim().split("\n").map((l) => l.split(/=(.*)/s).slice(0, 2)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 900 });
const clickText = async (t) => { for (const b of await page.$$("main button")) if ((await b.evaluate((x) => x.textContent.trim())) === t) { await b.click(); return true; } return false; };
const state = () => page.evaluate(() => ({
  confirm: document.querySelector(".confirm")?.getAttribute("aria-label") ?? null,
  actionDisabled: [...document.querySelectorAll(".confirm button.danger")].map((b) => b.disabled)[0] ?? null,
  errors: [...document.querySelectorAll(".error, [role=alert]")].map((e) => e.textContent.trim().slice(0, 70)).filter((t) => !t.includes("Dagster")),
  banner: !!document.querySelector(".banner"),
}));
const typeKey = async (k) => { const i = await page.$('.confirm input[type="password"]'); await i.click({ clickCount: 3 }); await i.press("Backspace"); await i.type(k); };
await page.goto("http://127.0.0.1:3710/#/ops", { waitUntil: "networkidle0" });
const out = [];
await clickText("로그 비우기"); await sleep(200); out.push(["비우기 확인 열림·키 없음", await state()]);
await typeKey(keys.QA_FREE); await clickText("비우기"); await sleep(800); out.push(["ops 권한 없는 키", await state()]);
await typeKey(keys.QA_ADMIN); await clickText("비우기"); await sleep(1200); out.push(["운영자 키로 비우기", await state()]);
await clickText("되돌리기"); await sleep(200); out.push(["되돌리기 확인 열림", await state()]);
await typeKey(keys.QA_ADMIN); await clickText("되돌리기"); await sleep(1200); out.push(["운영자 키로 되돌리기", await state()]);
for (const [k, v] of out) console.log(k.padEnd(18), JSON.stringify(v));
await browser.close();
