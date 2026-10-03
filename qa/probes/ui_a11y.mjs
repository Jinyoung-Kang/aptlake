// 화면 점검: 모든 메뉴·탭을 실제 Chrome 으로 열어 WCAG 2.1 AA(axe-core), 콘솔 오류, 실패한 요청, 중복 요청, 가로 넘침을 모은다.
//   QA_TOOLS=<axe-core·puppeteer-core 설치 폴더> node qa/probes/ui_a11y.mjs [기준 주소=http://127.0.0.1:3710] [--assert] > 결과.json
// --assert: axe 위반·콘솔 오류·실패한 요청·중복 요청·가로 넘침이 하나라도 있으면 종료 코드 1 (QA-010·011·012 재현 시험)
// 화면 폭 1280·375, 테마 밝게·어둡게(prefers-color-scheme). QA 스택(생성 데이터·조작 문자열 포함)만 대상으로 한다.
import { createRequire } from "node:module";

const req = createRequire(`${process.env.QA_TOOLS}/package.json`);
const puppeteer = req("puppeteer-core");
const axeSource = req("fs").readFileSync(req.resolve("axe-core/axe.min.js"), "utf8");
const ARGS = process.argv.slice(2);
const BASE = ARGS.find((a) => !a.startsWith("--")) || "http://127.0.0.1:3710";
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PAGES = (cx) => [
  "#/market", "#/region/11110", "#/region/11110?tab=distribution", "#/region/11110?tab=complexes",
  "#/trades?sgg=11110", "#/index", `#/complex/${cx}`, "#/quality", "#/dev", "#/ops",
];

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--no-sandbox"] });
const out = [];
try {
  const probe = await browser.newPage();
  await probe.goto(`${BASE}/v1/regions/11110/complexes?from=2025-01&to=2025-12&limit=1`, { waitUntil: "networkidle0" });
  const cx = JSON.parse(await probe.evaluate(() => document.body.innerText)).items?.[0]?.complexKey ?? "c_00000000000000000000";
  await probe.close();
  for (const [w, h] of [[1280, 900], [375, 812]]) {
    for (const scheme of ["light", "dark"]) {
      for (const path of PAGES(cx)) {
        const page = await browser.newPage();
        await page.setViewport({ width: w, height: h, isMobile: w < 768, hasTouch: w < 768 });
        await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
        const consoleErr = [], failed = [], urls = [];
        page.on("console", (m) => { if (m.type() === "error") consoleErr.push(m.text().slice(0, 200)); });
        page.on("pageerror", (e) => consoleErr.push(`pageerror: ${String(e).slice(0, 200)}`));
        page.on("requestfailed", (r) => failed.push(`${r.url().replace(BASE, "")} ${r.failure()?.errorText}`));
        page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.url().replace(BASE, "")} ${r.status()}`); });
        page.on("request", (r) => { if (r.url().includes("/v1/")) urls.push(r.url().replace(BASE, "")); });
        await page.goto(`${BASE}/${path}`, { waitUntil: "networkidle0", timeout: 60000 });
        await new Promise((r) => setTimeout(r, 800));
        await page.evaluate(axeSource);  // CSP(script-src 'self')가 인라인 주입을 막으므로 CDP 로 직접 실행 (CSP 는 그대로)
        const axe = await page.evaluate(async () => {
          const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] } });
          return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
            targets: v.nodes.slice(0, 3).map((n) => n.target.join(" ")) }));
        });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        const dup = Object.entries(urls.reduce((a, u) => ({ ...a, [u]: (a[u] || 0) + 1 }), {})).filter(([, n]) => n > 1);
        out.push({ path, width: w, scheme, axe, consoleErr, failed, duplicates: dup, apiRequests: urls.length, overflowPx: overflow });
        await page.close();
      }
    }
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out, null, 1));
if (ARGS.includes("--assert")) {
  const bad = out.filter((r) => r.axe.length || r.consoleErr.length || r.failed.length || r.duplicates.length || r.overflowPx > 0);
  for (const r of bad) console.error(`FAIL ${r.path} ${r.width} ${r.scheme}: axe ${r.axe.map((v) => v.id).join(",") || "-"} · 콘솔 ${r.consoleErr.length} · 실패 ${r.failed.length} · 중복 ${r.duplicates.map(([u, n]) => `${u}×${n}`).join(",") || "-"}`);
  console.error(`화면 조합 ${out.length}개 중 문제 ${bad.length}개`);
  process.exitCode = bad.length ? 1 : 0;
}
