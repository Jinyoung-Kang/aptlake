// 상호작용 점검: 화면마다 버튼·탭·칩·스위치·표 정렬·'더 보기'를 실제 Chrome 으로 모두 누르고, 그동안의 콘솔 오류·
// 실패한 요청·중복 요청(같은 주소를 1초 안에 두 번 이상)을 모은다. 키보드: Tab 40번 — 초점 표시가 없는 요소.
//   QA_TOOLS=<puppeteer-core 설치 폴더> node qa/probes/ui_interact.mjs [기준 주소] > 결과.json
// QA 스택 전용. '비우기' 같은 쓰기 동작은 누른 뒤 '되돌리기'까지 누른다 (QA 스택에서도 상태를 남기지 않게).
import { createRequire } from "node:module";

const req = createRequire(`${process.env.QA_TOOLS}/package.json`);
const puppeteer = req("puppeteer-core");
const BASE = process.argv[2] || "http://127.0.0.1:3710";
const PAGES = ["#/market", "#/region/11110", "#/region/11110?tab=distribution", "#/region/11110?tab=complexes",
  "#/trades?sgg=11110", "#/index", "#/quality", "#/dev", "#/ops"];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const out = [];
try {
  for (const width of [1280, 375]) {
    for (const path of PAGES) {
      const page = await browser.newPage();
      await page.setViewport({ width, height: 900, isMobile: width < 768, hasTouch: width < 768 });
      const errors = [], failed = [], reqs = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 160)); });
      page.on("pageerror", (e) => errors.push(`pageerror: ${String(e).slice(0, 160)}`));
      page.on("requestfailed", (r) => { if (!/aborted/i.test(r.failure()?.errorText ?? "")) failed.push(`${r.url().replace(BASE, "")} ${r.failure()?.errorText}`); });
      page.on("response", (r) => { if (r.status() >= 400) failed.push(`${r.request().method()} ${r.url().replace(BASE, "")} ${r.status()}`); });
      page.on("request", (r) => { if (r.url().includes("/v1/")) reqs.push([Date.now(), `${r.method()} ${r.url().replace(BASE, "")}`]); });
      await page.goto(`${BASE}/${path}`, { waitUntil: "networkidle0", timeout: 60000 });
      await sleep(500);
      // 누를 대상: 본문의 버튼(탭·칩·정렬 머리글·더 보기 포함)과 스위치. 위험한 이름은 따로 처리
      const labels = await page.$$eval("main button:not([disabled]), main [role=switch], main [role=tab]",
        (els) => els.filter((e) => e.offsetParent !== null).map((e, i) => `${i}|${(e.textContent || e.getAttribute("aria-label") || "").trim().slice(0, 30)}`));
      let clicked = 0;
      for (const item of labels) {
        const [idx, label] = [Number(item.split("|")[0]), item.split("|").slice(1).join("|")];
        const els = await page.$$("main button:not([disabled]), main [role=switch], main [role=tab]");
        const visible = [];
        for (const e of els) if (await e.evaluate((x) => x.offsetParent !== null)) visible.push(e);
        const el = visible[idx];
        if (!el) continue;
        if (/복사/.test(label)) continue;  // 클립보드 권한 — headless 에서 의미 없음
        try { await el.click(); clicked++; } catch { /* 가려진 요소 */ }
        await sleep(350);
        if (/비우기/.test(label)) {  // 운영 로그 비우기 → 바로 되돌리기
          const undo = await page.$$("main button");
          for (const u of undo) if (/되돌리기/.test(await u.evaluate((x) => x.textContent || ""))) { await u.click(); await sleep(350); break; }
        }
        await page.keyboard.press("Escape");
      }
      await sleep(800);
      // 중복 요청: 같은 주소가 1초 안에 두 번 이상
      const dup = [];
      reqs.sort((a, b) => a[0] - b[0]);
      for (let i = 0; i < reqs.length; i++) for (let j = i + 1; j < reqs.length && reqs[j][0] - reqs[i][0] < 1000; j++) if (reqs[j][1] === reqs[i][1]) dup.push(reqs[i][1]);
      // 키보드: Tab 40번 — 초점이 간 요소의 표시(outline·box-shadow)가 있는가
      let noFocusRing = [];
      if (width === 1280) {
        await page.goto(`${BASE}/${path}`, { waitUntil: "networkidle0" });
        await page.evaluate(() => document.body.focus());
        for (let i = 0; i < 40; i++) {
          await page.keyboard.press("Tab");
          const f = await page.evaluate(() => {
            const e = document.activeElement; if (!e || e === document.body) return null;
            const s = getComputedStyle(e);
            const ring = (s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0) || (s.boxShadow && s.boxShadow !== "none");
            return { sel: `${e.tagName.toLowerCase()}${e.className ? "." + String(e.className).split(" ")[0] : ""}`, txt: (e.textContent || e.getAttribute("aria-label") || "").trim().slice(0, 20), ring };
          });
          if (f && !f.ring) noFocusRing.push(`${f.sel} "${f.txt}"`);
        }
        noFocusRing = [...new Set(noFocusRing)];
      }
      out.push({ path, width, clicked, errors, failed: [...new Set(failed)], duplicates: [...new Set(dup)], noFocusRing });
      await page.close();
    }
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify(out, null, 1));
