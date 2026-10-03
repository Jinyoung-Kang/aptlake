// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradeQuery } from "../api/endpoints";

const page = (ids: string[], next: string | null) => ({
  items: ids.map((id) => ({ tradeId: id })), summary: { count: 9, cancelled: 0, medianPpm2: 1, medianPrice: 1 }, page: { nextCursor: next },
});

let pending: { url: string; resolve: (body: unknown) => void }[] = [];
beforeEach(() => {
  vi.resetModules();  // 클라이언트 조회 캐시를 테스트마다 새로
  pending = [];
  vi.stubGlobal("fetch", vi.fn((url: string) => new Promise((res) => {
    pending.push({ url, resolve: (body) => res(new Response(JSON.stringify(body), { status: 200 })) });
  })));
});
afterEach(() => vi.unstubAllGlobals());

async function mount(query: TradeQuery) {
  const { useTradesPager } = await import("./useTradesPager");
  let out!: ReturnType<typeof useTradesPager>;
  let setQ!: (q: TradeQuery) => void;
  const { useState } = await import("react");
  function Probe() {
    const [q, set] = useState(query);
    setQ = set;
    out = useTradesPager(q);
    return null;
  }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<Probe />));
  return { get: () => out, setQuery: (q: TradeQuery) => act(async () => setQ(q)), root };
}
const flush = (i: number, body: unknown) => act(async () => { pending[i].resolve(body); await Promise.resolve(); });

const Q: TradeQuery = { sgg: "41135", from: "2024-07-01", to: "2024-07-31", includeCancelled: true, limit: 2 };

describe("거래 목록 페이지 넘김", () => {
  it("더 보기는 커서로 이어 붙인다", async () => {
    const h = await mount(Q);
    await flush(0, page(["a", "b"], "c1"));
    expect(h.get().rows.map((r) => r.tradeId)).toEqual(["a", "b"]);
    await act(async () => h.get().more());
    expect(pending[1].url).toContain("&cursor=c1");
    await flush(1, page(["c"], null));
    expect(h.get().rows.map((r) => r.tradeId)).toEqual(["a", "b", "c"]);
    expect(h.get().cursor).toBeNull();
  });
  it("조건이 바뀐 뒤 도착한 '더 보기' 응답은 버린다", async () => {
    const h = await mount(Q);
    await flush(0, page(["a", "b"], "c1"));
    await act(async () => h.get().more());  // pending[1]
    await h.setQuery({ ...Q, sgg: "11110" });  // pending[2]
    await flush(2, page(["x"], null));
    await flush(1, page(["late"], null));
    expect(h.get().rows.map((r) => r.tradeId)).toEqual(["x"]);
  });
});
