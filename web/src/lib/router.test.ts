import { describe, expect, it } from "vitest";
import { href, parseHash } from "./router";

describe("해시 라우터", () => {
  it("경로·조각·쿼리", () => {
    const r = parseHash("#/region/41135?tab=distribution&ym=2024-07");
    expect(r.path).toBe("/region/41135");
    expect(r.parts).toEqual(["region", "41135"]);
    expect(r.params.get("tab")).toBe("distribution");
  });
  it("빈 해시는 시장 개요, 옛 주소는 새 주소로", () => {
    expect(parseHash("").path).toBe("/market");
    expect(parseHash("#trades").path).toBe("/trades");
  });
  it("href 는 빈 값을 빼고 인코딩한다", () => {
    expect(href("/trades", { sgg: "41135", area: "", cancel: null, q: "분당 구" })).toBe("#/trades?sgg=41135&q=%EB%B6%84%EB%8B%B9+%EA%B5%AC");
    expect(href("/market")).toBe("#/market");
  });
});
