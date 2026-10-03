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

function golden(name: string): { status: number; body: unknown; nextPage?: { body: unknown } } {
  // 서버 골든의 자리표시(최근 시각·오늘·추적 ID)를 고정값으로 — 화면 계산이 결정적이 되도록
  const text = readFileSync(resolve(GOLDEN, `${name}.json`), "utf8")
    .replaceAll('"<recent>"', '"2026-10-01T00:00:00+00:00"')
    .replaceAll('"<today>"', '"2026-10-01"')
    .replaceAll('"<trace>"', '"trace000000000000"');
  return JSON.parse(text);
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
  if (p === "/v1/trades") {
    const first = golden("trades_first_page");
    return q.get("cursor") ? (first.nextPage as { status: number; body: unknown }) : first;
  }
  if (/^\/v1\/trades\/.+\/history$/.test(p)) return g("trade_history");
  if (/^\/v1\/complexes\//.test(p)) return g("complex");
  if (p === "/v1/quality/summary") return g("quality_summary");
  if (p === "/v1/quality/rollup") return g("quality_rollup");
  if (p === "/v1/quality/partitions") return g("quality_grid_sido");
  if (/^\/v1\/quality\/partitions\/\d{5}\/\d{4}-\d{2}$/.test(p)) return g("quality_partition");
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
