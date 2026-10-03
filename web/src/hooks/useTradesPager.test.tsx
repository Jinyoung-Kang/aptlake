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

describe("조건을 빨리 바꿀 때 (취소된 요청)", () => {
  it("새 조건을 읽는 동안에는 이전 조건의 커서로 '더 보기'를 할 수 없다", async () => {
    // 취소 신호를 따르는 가짜 fetch — 취소되면 AbortError 로 끝난다 (실제 브라우저와 같게)
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => new Promise((res, rej) => {
      init?.signal?.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError")));
      pending.push({ url, resolve: (body) => res(new Response(JSON.stringify(body), { status: 200 })) });
    })));
    const h = await mount(Q);
    await flush(0, page(["a", "b"], "c1"));
    expect(h.get().cursor).toBe("c1");
    await h.setQuery({ ...Q, sgg: "11110" });  // pending[1] — 곧 취소됨
    await h.setQuery({ ...Q, sgg: "26110" });  // pending[2]
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(h.get().loading).toBe(true);  // 취소된 요청의 마무리가 '불러오는 중'을 끄면 안 된다
    expect(h.get().cursor).toBeNull();   // 이전 조건의 커서가 남으면 '더 보기'가 다른 조건의 다음 페이지를 부른다
  });
});
