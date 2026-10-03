// @vitest-environment jsdom
/**
 * 화면 특성 테스트: 페이지마다 서버 골든 응답으로 그린 DOM 을 스냅숏으로 고정한다.
 * 구조 정리(로직을 Hook·순수 함수로 옮기기) 전후에 화면 결과가 같아야 한다.
 * 차트는 캔버스 대신 build() 가 만든 옵션(JSON, 함수 제외)을 그려 데이터 계산까지 고정한다.
 * 의도한 화면 변경이면: npx vitest run -u  → 스냅숏 차이를 검토하고 커밋
 */
import { act } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installFetch, NOW, renderApp, requests } from "../test/app";

vi.mock("../charts/Chart", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../charts/Chart")>();
  return {
    ...mod,
    Chart: ({ build, label }: { build: (p: ReturnType<typeof mod.palette>) => unknown; label: string }) => (
      <pre data-chart={label}>{JSON.stringify(build(mod.palette()))}</pre>
    ),
  };
});

beforeAll(() => {
  window.scrollTo = () => undefined;
});
beforeEach(() => {
  vi.resetModules();  // 페이지마다 새 모듈 (API 클라이언트의 메모리 캐시가 다음 페이지로 넘어가지 않게)
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
  localStorage.clear();
  installFetch();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const PAGES: [string, string][] = [
  ["시장 개요", "#/market"],
  ["시장 개요 · 다른 달·지표", "#/market?ym=2024-06&metric=medianYoY"],
  ["지역 분석 · 개요", "#/region/41135"],
  ["지역 분석 · 가격 분포", "#/region/41135?tab=distribution&ym=2024-07"],
  ["지역 분석 · 단지", "#/region/41135?tab=complexes"],
  ["거래 목록", "#/trades?sgg=41135&from=2024-07-01&to=2024-07-31"],
  ["가격지수", "#/index"],
  ["단지", "#/complex/c_aaaaaaaaaaaaaaaaaaaa"],
  ["데이터 품질 · 파티션 상세", "#/quality?sido=41&cell=41135:202408"],
  ["수집 상태", "#/ops"],
  ["개발자", "#/dev"],
];

describe("화면 특성 (서버 골든 응답으로 그린 결과)", () => {
  it.each(PAGES)("%s", async (_name, hash) => {
    const { root, el } = await renderApp(hash);
    expect(el.innerHTML).toMatchSnapshot("dom");
    expect([...new Set(requests)].sort()).toMatchSnapshot("requests");
    await act(async () => root.unmount());
  });
});

describe("QA-014 보이는 글자와 접근 가능한 이름 (WCAG 2.5.3 Label in Name)", () => {
  // Lighthouse·axe 처럼: 보이는 글자(aria-hidden 제외)를 이어 붙여 공백만 하나로 정리한 뒤, 이름에 그대로 들어 있어야 한다.
  // (처음 수정은 'AptLake' 와 '아파트 실거래 데이터' 를 따로 봐서, 공백 없이 붙은 'AptLake아파트…' 를 놓쳤다 — 독립 검토)
  const norm = (t: string) => t.replace(/\s+/g, " ").trim();
  const visible = (el: Element): string =>
    [...el.childNodes].map((n) => (n.nodeType === 3 ? n.textContent ?? "" : (n as Element).getAttribute?.("aria-hidden") === "true" ? "" : visible(n as Element))).join("");
  it.each([["로고 링크", "a.brand"], ["테마 버튼", "button.theme-btn"]])("%s", async (_name, sel) => {
    const { root, el } = await renderApp("#/market");
    const node = el.querySelector(sel) as Element;
    const name = norm(node.getAttribute("aria-label") ?? visible(node));
    expect(name).toContain(norm(visible(node)));
    await act(async () => root.unmount());
  });
});
