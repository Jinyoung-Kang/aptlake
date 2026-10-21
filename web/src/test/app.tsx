/**
 * 화면 테스트 도구: 앱 전체를 jsdom 에 그리고, API 요청은 서버 골든 응답(api/tests/golden)으로 돌려준다.
 * 서버 골든과 같은 응답을 쓰므로 '실제 API 모양으로 화면이 어떻게 그려지는지'가 고정된다.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { vi } from "vitest";

const GOLDEN = resolve(__dirname, "../../../api/tests/golden");
export const NOW = "2026-10-01T00:30:00Z";

/** 서버 골든 응답 하나 (화면 시험이 기댓값을 골든 숫자에서 읽을 때도 쓴다) */
export function golden(name: string): { status: number; body: unknown; nextPage?: { body: unknown } } {
  // 서버 골든의 자리표시(최근 시각·오늘·추적 ID)를 고정값으로 — 화면 계산이 결정적이 되도록
  const text = readFileSync(resolve(GOLDEN, `${name}.json`), "utf8")
    .replaceAll('"<recent>"', '"2026-10-01T00:00:00+00:00"')
    .replaceAll('"<today>"', '"2026-10-01"')
    .replaceAll('"<trace>"', '"trace000000000000"');
  return JSON.parse(text);
}

type Body = Record<string, unknown>;
type Item = Record<string, unknown>;

/**
 * 골든에 없는 화면 상태는 골든을 고쳐 만든다. 상태를 정하는 값(발행 전 달 목록, 잠정 표시, 창 완결)은 서버 골든과
 * 상관없이 덮어쓰고, 그 밖의 숫자(계약 수·중앙값·표본 등)는 골든 그대로다 — 골든 숫자가 바뀌면 스냅숏도 바뀐다.
 *  - 시군구 11110 전월세: 2024-03 = 수집·발행 전(uncollectedMonths, 행 없음), 2024-04 = 계약 0건(어디에도 없음),
 *    2024-11 = 목록에 있지만 행이 있는 달(발행 기록이 서빙을 따라오지 못한 때 → 값 그대로), 2024-12 = 잠정 (잠정은 이 달만)
 *  - 시군구 11110 전세가율: 2024-07 = 창 미완결(complete=false → 값 null), 2024-08 = 잠정, 나머지는 완결이고
 *    값은 골든에서 값이 있는 달의 것
 */
function variant(name: "rent_months" | "jeonse_ratio") {
  const g = golden(name);
  const b = g.body as Body;
  if (name === "rent_months") {
    // provisional: undefined 는 응답 JSON 에서 빠진다 (골든의 잠정 표시를 지운다)
    b.items = (b.items as Item[]).filter((i) => i.dealYm !== "2024-03" && i.dealYm !== "2024-04")
      .map((i) => ({ ...i, provisional: i.dealYm === "2024-12" ? true : undefined }));
    b.uncollectedMonths = ["2024-03", "2024-11"];
  } else {
    const items = b.items as Item[];
    const full = items.find((i) => i.ratioPct != null);
    if (!full) throw new Error("jeonse_ratio 골든에 전세가율 값이 있는 달이 없다");
    const { tradeMedianPerM2, tradeSample, jeonseMedianPerM2, jeonseSample, ratioPct } = full;
    b.items = items.map((i) => (i.dealYm === "2024-07"
      ? { ...i, complete: false, tradeMedianPerM2: null, jeonseMedianPerM2: null, ratioPct: null, provisional: undefined }
      : { ...i, complete: true, tradeMedianPerM2, tradeSample, jeonseMedianPerM2, jeonseSample, ratioPct, provisional: i.dealYm === "2024-08" ? true : undefined }));
  }
  return g;
}

/** 경로(+일부 쿼리) → 골든 응답 이름 */
function route(url: URL): { status: number; body: unknown } {
  const p = url.pathname;
  const q = url.searchParams;
  const g = (n: string) => golden(n);
  if (p === "/v1/regions") return g("regions");
  if (p === "/v1/market/ticker") return g("market_ticker");
  if (p === "/v1/market/overview") return g(q.get("ym") === "2024-06" ? "market_overview_month" : "market_overview_default");
  if (p === "/v1/geo/sgg") return g("geo_sgg");
  if (p === "/v1/index/summary") return g("index_summary");
  if (p === "/v1/index") return g(q.get("regionId") === "00" ? "index_nation" : "index_missing");
  if (/^\/v1\/regions\/\d{5}\/months$/.test(p)) return g("region_months");
  if (/^\/v1\/regions\/\d{5}\/distribution$/.test(p)) return g(q.get("ym") === "2024-07" ? "distribution" : "distribution_empty");
  if (/^\/v1\/regions\/\d{5}\/complexes$/.test(p)) return g("region_complexes");
  // 전월세 (ADR-050)
  if (p === "/v1/regions/11110/rent/months") return variant("rent_months");
  if (p === "/v1/regions/11110/jeonse-ratio") return variant("jeonse_ratio");
  if (/^\/v1\/regions\/\d{5}\/rent\/months$/.test(p)) return g("rent_months");
  if (/^\/v1\/regions\/\d{5}\/jeonse-ratio$/.test(p)) return g("jeonse_ratio");
  if (/^\/v1\/regions\/\d{5}\/rent\/distribution$/.test(p)) return g(q.get("ym") === "2024-07" ? "rent_distribution" : "rent_distribution_empty");
  if (p === "/v1/rents") return g("rents_first_page");
  if (/^\/v1\/rents\/.+\/history$/.test(p)) return g("rent_history");
  if (p === "/v1/trades") {
    const first = golden("trades_first_page");
    return q.get("cursor") ? (first.nextPage as { status: number; body: unknown }) : first;
  }
  if (/^\/v1\/trades\/.+\/history$/.test(p)) return g("trade_history");
  if (p === "/v1/complexes/c_cccccccccccccccccccc") return g("complex_rent_only");  // 전월세에만 있는 단지 (토지임대부 미상)
  if (/^\/v1\/complexes\//.test(p)) return g("complex");
  if (p === "/v1/quality/summary") return g(q.get("source") === "rent" ? "quality_summary_rent" : "quality_summary");
  if (p === "/v1/quality/rollup") return g("quality_rollup");
  if (p === "/v1/quality/partitions") return g("quality_grid_sido");
  if (/^\/v1\/quality\/partitions\/\d{5}\/\d{4}-\d{2}$/.test(p)) return g(q.get("source") === "rent" ? "quality_partition_rent" : "quality_partition");
  if (p === "/v1/quality/latency") return g("quality_latency");
  if (p === "/v1/plans") return g("plans");
  if (p === "/v1/ops/status") return g("ops_status");
  if (p === "/v1/ops/errors") return g("ops_errors_pipeline");
  return { status: 404, body: { status: 404, code: "NOT_FOUND", title: "Not Found" } };
}

export const requests: string[] = [];

export function installFetch(): void {
  requests.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input, "http://t");
    requests.push(url.pathname + url.search);
    const r = route(url);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": "application/json" } });
  }));
}

/** 요청·지연 로딩이 모두 끝날 때까지 몇 번의 이벤트 루프를 돌린다. */
export async function settle(rounds = 25): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 2)); });
  }
}

export async function renderApp(hash: string): Promise<{ root: Root; el: HTMLElement }> {
  const { default: App } = await import("../App");
  window.location.hash = hash;
  const el = document.createElement("div");
  document.body.replaceChildren(el);
  const root = createRoot(el);
  await act(async () => { root.render(<App />); });
  // 페이지는 지연 로딩이다 — 모듈이 늦게 오면 고정 횟수만 기다린 스냅숏에 로딩 화면이 찍혔다 (모든 페이지에 page-head 가 있다)
  for (let i = 0; i < 400 && !el.querySelector(".page-head"); i++) await settle(1);
  await settle();
  return { root, el };
}
