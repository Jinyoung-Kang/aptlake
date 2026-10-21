// @vitest-environment jsdom
/**
 * 화면 특성 테스트: 페이지마다 서버 골든 응답으로 그린 DOM 을 스냅숏으로 고정한다.
 * 구조 정리(로직을 Hook·순수 함수로 옮기기) 전후에 화면 결과가 같아야 한다.
 * 차트는 캔버스 대신 build() 가 만든 옵션(JSON, 함수 제외)을 그려 데이터 계산까지 고정한다.
 * 의도한 화면 변경이면: npx vitest run -u  → 스냅숏 차이를 검토하고 커밋
 */
import { act } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComplexDetail, RentMonths } from "../api/types";
import { COUNT_INCOMPLETE_NOTE, PUBLISHED_BASIS, RATIO_INCOMPLETE_NOTE } from "../domain/rent";
import { manwon, num, ymLabel } from "../lib/format";
import { golden, installFetch, NOW, renderApp, requests, settle } from "../test/app";

// 마지막으로 그린 차트 옵션 (이름별) — 툴팁 문구처럼 JSON 에 남지 않는 함수를 시험할 때
const drawn = vi.hoisted(() => new Map<string, unknown>());

vi.mock("../charts/Chart", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../charts/Chart")>();
  return {
    ...mod,
    Chart: ({ build, label }: { build: (p: ReturnType<typeof mod.palette>) => unknown; label: string }) => {
      const option = build(mod.palette());
      drawn.set(label, option);
      return <pre data-chart={label}>{JSON.stringify(option)}</pre>;
    },
  };
});

beforeAll(() => {
  window.scrollTo = () => undefined;
});
beforeEach(() => {
  drawn.clear();  // 앞 시험이 그린 차트 옵션을 읽지 않게
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
  ["지역 분석 · 전월세", "#/region/41135?tab=rent&from=2024-01&to=2024-12"],
  ["지역 분석 · 전월세 (수집 전 달·0건 달·미완결 창·잠정)", "#/region/11110?tab=rent&from=2024-01&to=2024-12"],
  ["거래 목록", "#/trades?sgg=41135&from=2024-07-01&to=2024-07-31"],
  ["전월세 목록", "#/rents?sgg=41135&from=2024-07-01&to=2024-07-31"],
  ["가격지수", "#/index"],
  ["단지", "#/complex/c_aaaaaaaaaaaaaaaaaaaa"],
  ["단지 · 전월세만 (매매 거래 없음)", "#/complex/c_cccccccccccccccccccc"],
  ["데이터 품질 · 파티션 상세", "#/quality?sido=41&cell=41135:202408"],
  ["데이터 품질 · 전월세", "#/quality?source=rent&sido=41&cell=41135:202407"],
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

/** 정한 경로만 다른 응답으로 바꾸고 나머지는 골든 그대로 */
function override(match: string, res: () => Promise<Response>) {
  const served = globalThis.fetch as unknown as (input: string) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn((input: string) => (String(input).includes(match) ? res() : served(input))));
}

describe("지역 분석 전월세: 전세가율 요청만 실패해도 '불러오는 중'으로 남지 않는다", () => {
  it("전세가율 구역에 오류와 다시 시도를 보이고 골격은 지운다", async () => {
    override("/jeonse-ratio", async () => new Response(JSON.stringify({ status: 500, code: "INTERNAL", title: "Internal Server Error" }), { status: 500 }));
    const { root, el } = await renderApp("#/region/41135?tab=rent&from=2024-01&to=2024-12");
    const sec = [...el.querySelectorAll("section.section")].find((x) => x.querySelector("h2")?.textContent === "전세가율");
    expect(sec?.querySelector(".error button")?.textContent).toBe("다시 시도");
    expect(sec?.querySelector(".skeleton")).toBeNull();
    expect(el.querySelector("[data-chart='월별 전세가율 (3개월 창)']")).toBeNull();
    expect(el.querySelector("[data-chart^='월별 전세 m²당']")).not.toBeNull();  // 월별 전월세는 그대로
    await act(async () => root.unmount());
  });
});

describe("지역 분석 전월세: 발행된 적이 없는 달과 미완결 창은 0 이 아니라 빈칸, 툴팁에 까닭", () => {
  type Tip = { tooltip: { formatter: (ps: { dataIndex: number }[]) => string }; xAxis: { data: string[] } | { data: string[] }[] };
  const RENT = "월별 전세 m²당 보증금 중위수와 1~3사분위, 전세·월세 계약 수";
  const RATIO = "월별 전세가율 (3개월 창)";
  const tip = (label: string, ym: string) => {
    const o = drawn.get(label) as Tip;
    const x = Array.isArray(o.xAxis) ? o.xAxis[0].data : o.xAxis.data;
    expect(x).toContain(ym);
    return o.tooltip.formatter([{ dataIndex: x.indexOf(ym) }]);
  };
  // 골든의 월 값 (기댓값을 골든 숫자에서 읽는다 — 골든이 바뀌어도 시험이 숫자를 따로 고치지 않게)
  const goldenMonth = (ym: string) => (golden("rent_months").body as RentMonths).items.find((i) => i.dealYm === ym);
  it("발행 전 달·미완결 창의 툴팁", async () => {
    const { root, el } = await renderApp("#/region/11110?tab=rent&from=2024-01&to=2024-12");
    // 툴팁은 이번 렌더가 그린 옵션에서 읽는다 (drawn 은 시험마다 비운다)
    expect(el.querySelector(`[data-chart='${RENT}']`)).not.toBeNull();
    expect(el.querySelector(`[data-chart='${RATIO}']`)).not.toBeNull();
    expect(tip(RENT, "2024-03")).toContain("전월세 자료가 아직 발행된 적이 없는 달");
    expect(tip(RENT, "2024-04")).toContain("계약 0건");
    // 목록에 있어도 행이 있는 달은 발행된 값 그대로 (발행 전 문구 없음)
    expect(tip(RENT, "2024-11")).toContain(`<b>${num(goldenMonth("2024-11")?.jeonseMedianPerM2)}</b>`);
    expect(tip(RENT, "2024-11")).not.toContain("발행된 적이 없는 달");
    expect(tip(RATIO, "2024-07")).toContain("창 안에 매매·전월세 자료가 발행된 적이 없는 달");
    expect(tip(RATIO, "2024-08")).toContain("잠정");
    await act(async () => root.unmount());
  });
  it("최근 확정 월 전세 중위는 잠정 달(2024-12)이 아닌 그 앞 달(2024-11) 값", async () => {
    const { root, el } = await renderApp("#/region/11110?tab=rent&from=2024-01&to=2024-12");
    const v = [...el.querySelectorAll(".stat-row")].find((x) => x.querySelector(".k")?.textContent === "최근 확정 월 전세 중위")?.querySelector(".v");
    expect(v?.textContent).toBe(`${num(goldenMonth("2024-11")?.jeonseMedianPerM2)} 만원/m2`);
    await act(async () => root.unmount());
  });
});

describe("단지: 토지임대부 미상·전세가율 창 미완결", () => {
  const kpi = (el: HTMLElement, k: string) => [...el.querySelectorAll(".kpi")].find((x) => x.querySelector(".k")?.textContent === k);
  const ratioCells = (el: HTMLElement) => [...el.querySelectorAll("table")].find((t) => t.querySelector("caption")?.textContent === "전용면적 구간별")
    ?.querySelectorAll("tbody tr td:last-child");
  it("전월세에만 있는 단지: 토지임대부 미상, 매매 구역 대신 사실 문구, 전세가율 '–' 와 까닭", async () => {
    const { root, el } = await renderApp("#/complex/c_cccccccccccccccccccc");
    expect(kpi(el, "준공")?.querySelector(".d")?.textContent).toBe("토지임대부 여부 미상 (매매 원천에만 있는 항목)");
    expect(el.querySelector("[data-chart='단지 거래 이력 산점도']")).toBeNull();
    expect([...(ratioCells(el) ?? [])].map((td) => td.textContent)).toEqual(["–"]);
    expect(el.textContent).toContain("2023.08 ~ 2024.07 창 안에 매매·전월세 자료가 발행된 적이 없는 달");
    await act(async () => root.unmount());
  });
  it("창이 완결이면 구간별 전세가율 값을 그리고 까닭 문구는 없다 · 토지임대부 false = 일반", async () => {
    const g = golden("complex").body as ComplexDetail;
    const rent = g.rent as NonNullable<ComplexDetail["rent"]>;
    const a = rent.byArea[0];
    // 값은 골든의 두 중앙값으로 (API ratio_pct 와 같은 반올림)
    const pct = Math.round(((a.jeonseMedianPerM2 ?? 0) / (a.tradeMedianPerM2 ?? 1)) * 1000) / 10;
    const body = { ...g, rent: { ...rent, window: { ...rent.window, complete: true }, byArea: [{ ...a, ratioPct: pct }] } };
    override("/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa", async () => new Response(JSON.stringify(body), { status: 200 }));
    const { root, el } = await renderApp("#/complex/c_aaaaaaaaaaaaaaaaaaaa");
    expect(kpi(el, "준공")?.querySelector(".d")?.textContent).toBe("일반");
    expect([...(ratioCells(el) ?? [])].map((td) => td.textContent)).toEqual([`${num(pct, 1)}%`]);
    expect(el.textContent).not.toContain("발행된 적이 없는 달");
    await act(async () => root.unmount());
  });
  it("window.complete 가 없는 옛 모양 응답(배포 직후 결과 캐시·304)은 미완결로 보지 않는다 — 값은 그리고 '–' 는 표본 부족, 까닭 문구 없음", async () => {
    const g = golden("complex").body as ComplexDetail;
    const rent = g.rent as NonNullable<ComplexDetail["rent"]>;
    const a = rent.byArea[0];
    const pct = Math.round(((a.jeonseMedianPerM2 ?? 0) / (a.tradeMedianPerM2 ?? 1)) * 1000) / 10;
    // 필드를 넣기 전 API 모양: window 에 complete 없음, 구간별 ratioPct 는 값 또는 (표본 부족이면) null
    const { complete: _complete, ...oldWindow } = rent.window;
    const body = { ...g, rent: { ...rent, window: oldWindow, byArea: [{ ...a, ratioPct: pct }, { ...a, band: "85~135m²", ratioPct: null }] } };
    override("/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa", async () => new Response(JSON.stringify(body), { status: 200 }));
    const { root, el } = await renderApp("#/complex/c_aaaaaaaaaaaaaaaaaaaa");
    const cells = [...(ratioCells(el) ?? [])];
    expect(cells.map((td) => td.textContent)).toEqual([`${num(pct, 1)}%`, "–"]);
    expect(cells[1].querySelector("[title]")).toBeNull();  // 표본 부족의 '–' 에 창 미완결 까닭을 달지 않는다
    expect(el.textContent).not.toContain("발행된 적이 없는 달");
    await act(async () => root.unmount());
  });
});

describe("단지 전월세: 구간이 없을 때 창이 미완결이면 '이 기간 계약 없음'으로 단정하지 않는다", () => {
  const sub = (el: HTMLElement) => [...el.querySelectorAll("section.section")].find((x) => x.querySelector("h2")?.textContent === "전월세")
    ?.querySelector(".section-head .sub")?.textContent;
  const emptyText = (el: HTMLElement) => [...el.querySelectorAll("table")].find((t) => t.querySelector("caption")?.textContent === "전용면적 구간별")
    ?.querySelector(".empty")?.textContent;
  /** 골든 단지에서 전월세 계약만 비운 응답 (창 완결 여부는 시험마다) — 창의 앞뒤 달을 돌려준다 */
  const serveEmpty = (complete: boolean | undefined) => {
    const g = golden("complex").body as ComplexDetail;
    const rent = g.rent as NonNullable<ComplexDetail["rent"]>;
    const { complete: _complete, ...win } = rent.window;
    const body = { ...g, rent: { ...rent, window: complete === undefined ? win : { ...win, complete }, contracts: 0, byArea: [], recent: [] } };
    override("/v1/complexes/c_aaaaaaaaaaaaaaaaaaaa", async () => new Response(JSON.stringify(body), { status: 200 }));
    return win;
  };
  it("미완결: 머리글은 발행된 계약 기준, 빈 표는 0건이 확정이 아니라고 적는다", async () => {
    const win = serveEmpty(false);
    const { root, el } = await renderApp("#/complex/c_aaaaaaaaaaaaaaaaaaaa");
    expect(sub(el)).toContain(`계약 0건 (${PUBLISHED_BASIS}) ·`);
    expect(emptyText(el)).toBe(`발행된 전월세 계약이 없습니다 — ${ymLabel(win.from)} ~ ${ymLabel(win.to)} ${COUNT_INCOMPLETE_NOTE}.`);
    expect(emptyText(el)).toContain("0건이 확정은 아님");
    expect(el.textContent).not.toContain("이 기간 전월세 계약이 없습니다");
    expect(el.textContent).not.toContain(RATIO_INCOMPLETE_NOTE);  // 비율 각주는 구간이 있을 때만 (빈 표 문구가 까닭을 적는다)
    await act(async () => root.unmount());
  });
  it.each([["완결", true], ["complete 가 없는 옛 모양 응답 (미완결로 보지 않는다)", undefined]] as const)(
    "%s: 0건은 사실 — '이 기간 계약 없음', 기준 표시 없음", async (_name, complete) => {
      serveEmpty(complete);
      const { root, el } = await renderApp("#/complex/c_aaaaaaaaaaaaaaaaaaaa");
      expect(sub(el)).toContain("계약 0건 ·");
      expect(emptyText(el)).toBe("이 기간 전월세 계약이 없습니다.");
      expect(el.textContent).not.toContain(PUBLISHED_BASIS);
      expect(el.textContent).not.toContain("0건이 확정은 아님");
      await act(async () => root.unmount());
    });
});

describe("전월세 목록 상세: 원천이 비운 값은 '미상' (버전 이력과 같게)", () => {
  it("갱신요구권·종전 월세가 비어 있으면 미상, 종전 보증금은 값", async () => {
    const { root, el } = await renderApp("#/rents?sgg=41135&from=2024-07-01&to=2024-07-31");
    // 골든에서 종전 보증금은 있고 종전 월세·갱신요구권은 빈 계약을 고른다
    type R = { rentId: string; previousDepositManwon: number | null; previousMonthlyRentManwon: number | null; renewalRightUsed: string | null; contractTerm: string | null };
    const items = (golden("rents_first_page").body as { items: R[] }).items;
    const idx = items.findIndex((r) => r.previousDepositManwon != null && r.previousMonthlyRentManwon == null && r.renewalRightUsed == null);
    expect(idx).toBeGreaterThanOrEqual(0);
    const row = el.querySelectorAll("table tbody tr")[idx] as HTMLTableRowElement;
    await act(async () => row.click());
    await settle();
    const stat = (k: string) => [...el.querySelectorAll(".drawer .stat-row")].find((x) => x.querySelector(".k")?.textContent === k)?.querySelector(".v")?.textContent;
    expect(stat("갱신요구권")).toBe("미상");
    expect(stat("계약 기간")).toBe(items[idx].contractTerm ?? "미상");
    expect(stat("종전 보증금 / 월세")).toBe(`${manwon(items[idx].previousDepositManwon)} / 미상`);
    await act(async () => root.unmount());
  });
});

describe("데이터 품질: 원천을 바꾼 직후 이전 원천의 요약·검사·완결도는 흐리게", () => {
  it("전월세 요약이 오기 전에는 매매 요약·실패 검사 표가 data-stale", async () => {
    override("/v1/quality/summary?source=rent", () => new Promise<Response>(() => {}));
    const { root, el } = await renderApp("#/quality");
    expect(el.querySelector(".kpis")?.getAttribute("data-stale")).toBe("false");
    const rent = [...el.querySelectorAll("fieldset.seg button")].find((b) => b.textContent === "전월세") as HTMLButtonElement;
    await act(async () => rent.click());
    await settle();
    expect(window.location.hash).toContain("source=rent");
    expect(el.querySelector(".kpis")?.getAttribute("data-stale")).toBe("true");
    expect(el.querySelector(".kpis")?.getAttribute("aria-busy")).toBe("true");
    expect(el.querySelector("table")?.closest("[data-stale]")?.getAttribute("data-stale")).toBe("true");
    await act(async () => root.unmount());
  });
});

describe("QA-013 거래 목록: 첫 결과가 오기 전에도 요약 줄·표 자리를 둔다 (레이아웃 이동)", () => {
  it("조회 가능 범위(시세 띠)가 오기 전 — 요약 줄 자리가 있고 빈 표가 먼저 그려지지 않는다", async () => {
    const served = globalThis.fetch as unknown as (input: string) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn((input: string) => (String(input).includes("/v1/market/ticker") ? new Promise<Response>(() => {}) : served(input))));
    const { root, el } = await renderApp("#/trades?sgg=41135");
    expect(el.querySelector(".kpis")).not.toBeNull();
    expect(el.querySelector("table.t")).toBeNull();
    await act(async () => root.unmount());
  });
});
