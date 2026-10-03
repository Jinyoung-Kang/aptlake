import { describe, expect, it } from "vitest";
import { CONNECTIVITY_PROBE, etagProbe, probes, reportText, type Result, summarize } from "./apiProbes";

const res = (status: number, headers: Record<string, string> = {}) => new Response(null, { status, headers });

describe("API 연결 테스트 판정", () => {
  const list = probes("41135", "2024-07", "");
  const by = (id: string) => list.find((p) => p.id === id)!;
  it("점검 목록: 키를 넣었을 때만 '내 키' 점검", () => {
    expect(list.map((p) => p.id)).toEqual(["regions", "overview", "months", "trades", "index", "bad-param", "bad-key"]);
    expect(probes("41135", "2024-07", "al_live_x").at(-1)?.path).toBe("/v1/me/usage?days=1");
    expect(by("months").path).toBe("/v1/regions/41135/months?from=2023-08&to=2024-07");
  });
  it("계약 확인: 버전 헤더 = 본문 버전, 잘못된 요청은 4xx Problem Details, 잘못된 키는 401", () => {
    expect(by("months").check(res(200, { "x-dataset-version": "gold@1" }), { datasetVersion: "gold@1" })).toBeNull();
    expect(by("months").check(res(200, { "x-dataset-version": "gold@1" }), { datasetVersion: "gold@2" })).toContain("다름");
    expect(by("bad-param").check(res(422, { "content-type": "application/problem+json" }), null)).toBeNull();
    expect(by("bad-param").check(res(422, { "content-type": "application/json" }), null)).toContain("Problem Details");
    expect(by("bad-key").check(res(401), { code: "INVALID_API_KEY" })).toBeNull();
    expect(by("regions").check(res(200), { items: [] })).toBe("목록이 비어 있음");
    expect(CONNECTIVITY_PROBE.check(res(503), null)).toBe("HTTP 503");
    expect(etagProbe(by("months")).check(res(304), null)).toBeNull();
  });
  it("요약·보고서", () => {
    const r = (o: Partial<Result>): Result => ({ id: "x", name: "시군구 목록", path: "/v1/regions", expect: "", status: 200, ms: 10, pass: true, reason: "통과",
      cache: null, version: null, remaining: null, limit: null, bytes: 1, ...o });
    const results = [r({ ms: 30, version: "gold@1" }), r({ ms: 10 }), r({ pass: false, status: 500, ms: null, reason: "HTTP 500" })];
    expect(summarize(results)).toEqual({ passed: 2, median: 30, version: "gold@1" });
    const text = reportText({ at: "2026-10-01T00:30:00Z", results, conn: null, connErr: "닿지 못함", withKey: false });
    expect(text.split("\n").slice(0, 2)).toEqual(["# AptLake API 연결 테스트 — 2026-10-01 09:30:00 KST", "공개 API 2/3 통과 · 지연 중앙값 30ms · 데이터셋 gold@1 · 웹 키(BFF)"]);
    expect(text).toContain("- 닿지 못함");
    expect(text).toContain("- 실패 시군구 목록: 500 · – · /v1/regions · HTTP 500");
  });
});
