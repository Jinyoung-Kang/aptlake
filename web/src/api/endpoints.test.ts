import { describe, expect, it } from "vitest";
import { paths } from "./endpoints";

describe("공개 API 경로", () => {
  it("거래 목록: 면적 조건은 있을 때만, 커서는 맨 끝에 인코딩해서", () => {
    const q = { sgg: "41135", from: "2024-07-01", to: "2024-07-31", includeCancelled: true, limit: 100 };
    expect(paths.trades(q)).toBe("/v1/trades?sggCd=41135&from=2024-07-01&to=2024-07-31&includeCancelled=true&limit=100");
    expect(paths.trades({ ...q, minArea: 60, maxArea: 84.9999, cursor: "a+b/c=" }))
      .toBe("/v1/trades?sggCd=41135&from=2024-07-01&to=2024-07-31&includeCancelled=true&limit=100&minArea=60&maxArea=84.9999&cursor=a%2Bb%2Fc%3D");
    expect(paths.trades({ ...q, minArea: 0 })).toContain("&minArea=0");  // 0 도 조건이다
  });
  it("검색어는 인코딩, 기준월이 없으면 쿼리 없이", () => {
    expect(paths.search("래미안 1단지")).toBe("/v1/search?q=%EB%9E%98%EB%AF%B8%EC%95%88%201%EB%8B%A8%EC%A7%80");
    expect(paths.marketOverview(null)).toBe("/v1/market/overview");
    expect(paths.marketOverview("2024-06")).toBe("/v1/market/overview?ym=2024-06");
  });
});
