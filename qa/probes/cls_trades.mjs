// 거래 목록 레이아웃 이동(CLS) 반복 측정 — 데스크톱(지연 40ms)·모바일(CPU 4배·지연 150ms) 각 4회, 밀린 요소와 위치 변화를 함께 남긴다.
//   QA_TOOLS=<puppeteer-core 설치 폴더> node qa/probes/cls_trades.mjs   → 한 회라도 CLS > 0.1 이면 종료 코드 1 (QA-013 재현 시험)
import { createRequire } from "node:module";
const req = createRequire(`${process.env.QA_TOOLS}/package.json`);
const puppeteer = req("puppeteer-core");
let worst = 0;
const browser = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
for (const [w, h, mobile] of [[1350, 940, false], [412, 823, true]]) {
  for (let run = 0; run < 4; run++) {
    const page = await browser.newPage();
    await page.setViewport({ width: w, height: h, isMobile: mobile, hasTouch: mobile });
    const cdp = await page.createCDPSession();
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: mobile ? 4 : 1 });
    await cdp.send("Network.enable");
    await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: mobile ? 150 : 40, downloadThroughput: mobile ? 200000 : 1250000, uploadThroughput: 100000 });
    await page.evaluateOnNewDocument(() => {
      window.__ls = [];
      new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__ls.push({ v: +e.value.toFixed(3), t: Math.round(e.startTime),
        src: (e.sources || []).map((s) => `${(s.node?.className || s.node?.nodeName || "?").toString().slice(0, 30)}:${Math.round(s.previousRect.y)}→${Math.round(s.currentRect.y)}(h${Math.round(s.previousRect.height)}→${Math.round(s.currentRect.height)})`) }))).observe({ type: "layout-shift", buffered: true });
    });
    await page.goto("http://127.0.0.1:3710/#/trades?sgg=11110", { waitUntil: "networkidle0" });
    await new Promise((r) => setTimeout(r, 1500));
    const ls = await page.evaluate(() => window.__ls);
    const total = ls.reduce((a, e) => a + e.v, 0);
    worst = Math.max(worst, total);
    console.log(`${mobile ? "모바일" : "데스크톱"} #${run} CLS합 ${total.toFixed(3)}`, JSON.stringify(ls.filter((e) => e.v >= 0.01)).slice(0, 600));
    await page.close();
  }
}
await browser.close();
console.log(`최대 CLS ${worst.toFixed(3)} — ${worst > 0.1 ? "실패 (> 0.1)" : "통과"}`);
process.exitCode = worst > 0.1 ? 1 : 0;
