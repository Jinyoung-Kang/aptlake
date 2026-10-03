// API 연결 테스트의 점검 목록·판정·보고서 문구 (React 무관). 실제 요청은 api/probe.ts, 실행 순서는 hooks/useApiTest.ts.
import { paths } from "../api/endpoints";
import type { Connectivity } from "../api/types";
import { DASH, kst, lastDay, num, ymAdd } from "../lib/format";

export type Probe = {
  id: string; name: string; path: string; expect: string; headers?: Record<string, string>;
  /** 통과면 null, 아니면 실패 이유 */
  check: (r: Response, body: unknown) => string | null;
};
export type Result = {
  id: string; name: string; path: string; expect: string; status: number | null; ms: number | null; pass: boolean; reason: string;
  cache: string | null; version: string | null; remaining: string | null; limit: string | null; bytes: number | null;
};

const isProblem = (r: Response) => (r.headers.get("content-type") ?? "").startsWith("application/problem+json");
const has = (body: unknown, key: string) => typeof body === "object" && body !== null && key in body;
const arr = (body: unknown, key: string) => (has(body, key) ? (body as Record<string, unknown>)[key] : null);

export function probes(sgg: string, to: string, key: string): Probe[] {
  const from = ymAdd(to, -11);
  const months = paths.regionMonths(sgg, from, to);
  const ok200 = (r: Response) => (r.status === 200 ? null : `HTTP ${r.status}`);
  // 거래(limit 5, 해제 조건 생략)·잘못된 매개변수·잘못된 키는 일부러 화면과 다른 모양이라 경로를 직접 적는다
  const list: Probe[] = [
    { id: "regions", name: "시군구 목록", path: paths.regions(), expect: "200 · 목록 1개 이상",
      check: (r, b) => ok200(r) ?? (Array.isArray(arr(b, "items")) && (arr(b, "items") as unknown[]).length > 0 ? null : "목록이 비어 있음") },
    { id: "overview", name: "시장 개요 (ClickHouse 집계)", path: paths.marketOverview(null), expect: "200 · 전국 요약 포함",
      check: (r, b) => ok200(r) ?? (arr(b, "nation") ? null : "전국 요약 없음") },
    { id: "months", name: "지역 월 통계", path: months, expect: "200 · 버전 헤더 = 본문 버전",
      check: (r, b) => ok200(r) ?? (r.headers.get("x-dataset-version") === arr(b, "datasetVersion") ? null : "X-Dataset-Version 과 본문 버전이 다름") },
    { id: "trades", name: "거래 목록 (커서 페이지)", path: `/v1/trades?sggCd=${sgg}&from=${to}-01&to=${lastDay(to)}&limit=5`, expect: "200 · page 객체",
      check: (r, b) => ok200(r) ?? (arr(b, "page") ? null : "page 객체 없음") },
    { id: "index", name: "가격지수", path: paths.index("00"), expect: "200 · 시계열 포함",
      check: (r, b) => ok200(r) ?? (Array.isArray(arr(b, "series")) ? null : "series 없음") },
    { id: "bad-param", name: "잘못된 매개변수 거절", path: `/v1/regions/${sgg}/months?from=${to}&to=2000-13`, expect: "400·422 + Problem Details",
      check: (r) => ([400, 422].includes(r.status) && isProblem(r) ? null : `HTTP ${r.status} (4xx Problem Details 기대)`) },
    { id: "bad-key", name: "잘못된 키 거절", path: "/v1/regions", expect: "401 INVALID_API_KEY", headers: { "X-API-Key": "al_live_invalid" },
      check: (r, b) => (r.status === 401 && arr(b, "code") === "INVALID_API_KEY" ? null : `HTTP ${r.status} (401 기대)`) },
  ];
  if (key) {
    list.push({ id: "my-key", name: "내 키 인증 · 사용량", path: paths.myUsage(1), expect: "200 · 플랜 확인",
      check: (r, b) => ok200(r) ?? (arr(b, "plan") ? null : "플랜 정보 없음") });
  }
  return list;
}

/** 서버 구성요소 점검 — 이 요청 자체가 'API 서버 도달' 점검도 겸한다 ('ops' 스코프 → 웹 화면 키로) */
export const CONNECTIVITY_PROBE: Probe = {
  id: "conn", name: "API 서버 도달 (구성요소 점검)", path: paths.opsConnectivity(), expect: "200 · 구성요소 응답",
  check: (r, b) => (r.status === 200 ? (has(b, "items") ? null : "응답 형식 다름") : `HTTP ${r.status}`),
};

/** 같은 요청을 ETag 로 재검증 → 304 (본문 없이 '바뀌지 않음') */
export function etagProbe(months: Probe): Probe {
  return { ...months, id: "etag", name: "캐시 재검증 (ETag → 304)", expect: "304 Not Modified",
    check: (res) => (res.status === 304 ? null : `HTTP ${res.status} (304 기대)`) };
}

export function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

/** 결과 요약: 통과 수, 지연 중앙값, 응답의 자료 버전 */
export function summarize(results: Result[]) {
  return {
    passed: results.filter((r) => r.pass).length,
    median: median(results.filter((r) => r.ms != null).map((r) => r.ms as number)),
    version: results.find((r) => r.version)?.version ?? null,
  };
}

/** 복사용 보고서 */
export function reportText(o: { at: string | null; results: Result[]; conn: Connectivity | null; connErr: string | null; withKey: boolean }): string {
  const { passed, median: med, version } = summarize(o.results);
  const lines = [
    `# AptLake API 연결 테스트 — ${kst(o.at)} KST`,
    `공개 API ${passed}/${o.results.length} 통과 · 지연 중앙값 ${med == null ? DASH : `${num(med)}ms`} · 데이터셋 ${version ?? DASH}${o.withKey ? " · 내 키 사용" : " · 웹 키(BFF)"}`,
    "",
    "## 서버 구성요소",
    ...(o.conn ? o.conn.items.map((i) => `- ${i.ok ? "정상" : "실패"} ${i.name}: ${i.latencyMs == null ? DASH : `${i.latencyMs}ms`}${i.note ? ` · ${i.note}` : ""}${i.error ? ` · ${i.error}` : ""}`) : [`- ${o.connErr ?? "결과 없음"}`]),
    ...(o.conn ? o.conn.sources.map((s) => `- 원천 ${s.name}: 마지막 성공 ${kst(s.lastSuccessAt, false)} · ${s.state}${s.detail ? ` · ${s.detail}` : ""}`) : []),
    "",
    "## 공개 API (브라우저 → 웹 서버 → API)",
    ...o.results.map((r) => `- ${r.pass ? "통과" : "실패"} ${r.name}: ${r.status ?? DASH} · ${r.ms == null ? DASH : `${num(r.ms)}ms`} · ${r.path}${r.pass ? "" : ` · ${r.reason}`}`),
  ];
  return lines.join("\n");
}
