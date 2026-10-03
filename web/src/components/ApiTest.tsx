import { useState } from "react";
import { useAvailable } from "../hooks/useApi";
import { DASH, kst, lastDay, num, relative, ymAdd } from "../lib/format";
import { recentRegions } from "../lib/regions";
import { DataTable } from "./DataTable";
import { Badge, CopyButton } from "./ui";
import type { Connectivity } from "../api/types";

/**
 * API 연결 테스트 (수집 상태 메뉴).
 *  1) 서버 구성요소: API 서버가 운영 DB·캐시·서빙 DB·오케스트레이터에 닿는지 (/v1/ops/connectivity, 서버 안에서 측정)
 *  2) 공개 API 점검: 이 브라우저 → 웹 서버(BFF) → API 경로로 실제 요청을 보내 상태 코드·지연·헤더 계약을 확인
 * 외부 원천 API 는 호출하지 않는다 (일일 한도 보호) — 서버가 기록한 마지막 성공 수집 시각만 보여 준다.
 */

type Probe = {
  id: string; name: string; path: string; expect: string; headers?: Record<string, string>;
  /** 통과면 null, 아니면 실패 이유 */
  check: (r: Response, body: unknown) => string | null;
};
type Result = {
  id: string; name: string; path: string; expect: string; status: number | null; ms: number | null; pass: boolean; reason: string;
  cache: string | null; version: string | null; remaining: string | null; limit: string | null; bytes: number | null;
};

const isProblem = (r: Response) => (r.headers.get("content-type") ?? "").startsWith("application/problem+json");
const has = (body: unknown, key: string) => typeof body === "object" && body !== null && key in body;
const arr = (body: unknown, key: string) => (has(body, key) ? (body as Record<string, unknown>)[key] : null);

function probes(sgg: string, to: string, key: string): Probe[] {
  const from = ymAdd(to, -11);
  const months = `/v1/regions/${sgg}/months?from=${from}&to=${to}`;
  const ok200 = (r: Response) => (r.status === 200 ? null : `HTTP ${r.status}`);
  const list: Probe[] = [
    { id: "regions", name: "시군구 목록", path: "/v1/regions", expect: "200 · 목록 1개 이상",
      check: (r, b) => ok200(r) ?? (Array.isArray(arr(b, "items")) && (arr(b, "items") as unknown[]).length > 0 ? null : "목록이 비어 있음") },
    { id: "overview", name: "시장 개요 (ClickHouse 집계)", path: "/v1/market/overview", expect: "200 · 전국 요약 포함",
      check: (r, b) => ok200(r) ?? (arr(b, "nation") ? null : "전국 요약 없음") },
    { id: "months", name: "지역 월 통계", path: months, expect: "200 · 버전 헤더 = 본문 버전",
      check: (r, b) => ok200(r) ?? (r.headers.get("x-dataset-version") === arr(b, "datasetVersion") ? null : "X-Dataset-Version 과 본문 버전이 다름") },
    { id: "trades", name: "거래 목록 (커서 페이지)", path: `/v1/trades?sggCd=${sgg}&from=${to}-01&to=${lastDay(to)}&limit=5`, expect: "200 · page 객체",
      check: (r, b) => ok200(r) ?? (arr(b, "page") ? null : "page 객체 없음") },
    { id: "index", name: "가격지수", path: "/v1/index?regionId=00", expect: "200 · 시계열 포함",
      check: (r, b) => ok200(r) ?? (Array.isArray(arr(b, "series")) ? null : "series 없음") },
    { id: "bad-param", name: "잘못된 매개변수 거절", path: `/v1/regions/${sgg}/months?from=${to}&to=2000-13`, expect: "400·422 + Problem Details",
      check: (r) => ([400, 422].includes(r.status) && isProblem(r) ? null : `HTTP ${r.status} (4xx Problem Details 기대)`) },
    { id: "bad-key", name: "잘못된 키 거절", path: "/v1/regions", expect: "401 INVALID_API_KEY", headers: { "X-API-Key": "al_live_invalid" },
      check: (r, b) => (r.status === 401 && arr(b, "code") === "INVALID_API_KEY" ? null : `HTTP ${r.status} (401 기대)`) },
  ];
  if (key) {
    list.push({ id: "my-key", name: "내 키 인증 · 사용량", path: "/v1/me/usage?days=1", expect: "200 · 플랜 확인",
      check: (r, b) => ok200(r) ?? (arr(b, "plan") ? null : "플랜 정보 없음") });
  }
  return list;
}

async function runProbe(p: Probe, key: string, etag?: string | null): Promise<Result & { etag: string | null; body: unknown }> {
  const headers: Record<string, string> = { Accept: "application/json", ...(key && !p.headers ? { "X-API-Key": key } : {}), ...(p.headers ?? {}) };
  if (etag) headers["If-None-Match"] = etag;
  const t0 = performance.now();
  const base = { id: p.id, name: p.name, path: p.path, expect: p.expect };
  try {
    const r = await fetch(p.path, { headers, cache: "no-store" });
    const text = await r.text();
    const ms = performance.now() - t0;
    let body: unknown = null;
    try { body = text ? JSON.parse(text) : null; } catch { /* JSON 이 아닌 본문(프록시 오류 페이지 등) */ }
    const reason = p.check(r, body);
    return {
      ...base, status: r.status, ms, pass: reason == null, reason: reason ?? "통과",
      cache: r.headers.get("x-cache"), version: r.headers.get("x-dataset-version"),
      remaining: r.headers.get("x-ratelimit-remaining"), limit: r.headers.get("x-ratelimit-limit"),
      bytes: text.length, etag: r.headers.get("etag"), body,
    };
  } catch (e) {
    return { ...base, status: null, ms: null, pass: false, reason: `연결 실패 (${(e as Error).name})`, cache: null, version: null, remaining: null, limit: null, bytes: null, etag: null, body: null };
  }
}

/** 표에는 결과만 남긴다 (응답 본문은 버림). */
function slim(r: Result & { body?: unknown; etag?: string | null }): Result {
  const { body: _body, etag: _etag, ...rest } = r;
  return rest;
}

function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

export default function ApiTest() {
  const avail = useAvailable();
  const [key, setKey] = useState("");  // 메모리에만 (저장·URL 에 쓰지 않음)
  const [running, setRunning] = useState(false);
  const [conn, setConn] = useState<Connectivity | null>(null);
  const [connErr, setConnErr] = useState<string | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [at, setAt] = useState<string | null>(null);

  const run = async () => {
    setRunning(true); setResults([]); setConn(null); setConnErr(null);
    const k = key.trim();
    // 1) 서버 구성요소 (이 요청 자체가 'API 서버 도달' 점검도 겸한다)
    const c = await runProbe({ id: "conn", name: "API 서버 도달 (구성요소 점검)", path: "/v1/ops/connectivity", expect: "200 · 구성요소 응답",
      check: (r, b) => (r.status === 200 ? (has(b, "items") ? null : "응답 형식 다름") : `HTTP ${r.status}`) }, "");
    // ↑ 구성요소 점검은 'ops' 스코프가 필요한 운영 정보 → 사용자 키가 아니라 웹 화면(BFF) 키로 요청한다
    if (c.pass) setConn(c.body as Connectivity);
    else setConnErr(c.status == null ? "구성요소 점검 요청이 서버에 닿지 못했습니다 (웹 서버 또는 API 가 꺼져 있음)." : `구성요소 점검 실패: ${c.reason}`);
    // 2) 공개 API — 순서대로 (동시에 보내면 지연이 서로 섞인다)
    const sgg = recentRegions()[0] ?? "11110";
    const to = avail?.default ?? avail?.to ?? ymAdd(new Date().toISOString().slice(0, 7), -3);
    const out: Result[] = [slim(c)];
    let monthsEtag: string | null = null;
    for (const p of probes(sgg, to, k)) {
      const r = await runProbe(p, k);
      if (p.id === "months") monthsEtag = r.etag;
      out.push(slim(r));
      setResults([...out]);
    }
    // 3) 같은 요청을 ETag 로 재검증 → 304 (본문 없이 '바뀌지 않음')
    const months = probes(sgg, to, k).find((p) => p.id === "months")!;
    if (monthsEtag) {
      const r = await runProbe({ ...months, id: "etag", name: "캐시 재검증 (ETag → 304)", expect: "304 Not Modified",
        check: (res) => (res.status === 304 ? null : `HTTP ${res.status} (304 기대)`) }, k, monthsEtag);
      out.push(slim(r));
    }
    setResults([...out]);
    setAt(new Date().toISOString());
    setRunning(false);
  };

  const passed = results.filter((r) => r.pass).length;
  const med = median(results.filter((r) => r.ms != null).map((r) => r.ms as number));
  const version = results.find((r) => r.version)?.version ?? null;

  const report = () => {
    const lines = [
      `# AptLake API 연결 테스트 — ${kst(at)} KST`,
      `공개 API ${passed}/${results.length} 통과 · 지연 중앙값 ${med == null ? DASH : `${num(med)}ms`} · 데이터셋 ${version ?? DASH}${key.trim() ? " · 내 키 사용" : " · 웹 키(BFF)"}`,
      "",
      "## 서버 구성요소",
      ...(conn ? conn.items.map((i) => `- ${i.ok ? "정상" : "실패"} ${i.name}: ${i.latencyMs == null ? DASH : `${i.latencyMs}ms`}${i.note ? ` · ${i.note}` : ""}${i.error ? ` · ${i.error}` : ""}`) : [`- ${connErr ?? "결과 없음"}`]),
      ...(conn ? conn.sources.map((s) => `- 원천 ${s.name}: 마지막 성공 ${kst(s.lastSuccessAt, false)} · ${s.state}${s.detail ? ` · ${s.detail}` : ""}`) : []),
      "",
      "## 공개 API (브라우저 → 웹 서버 → API)",
      ...results.map((r) => `- ${r.pass ? "통과" : "실패"} ${r.name}: ${r.status ?? DASH} · ${r.ms == null ? DASH : `${num(r.ms)}ms`} · ${r.path}${r.pass ? "" : ` · ${r.reason}`}`),
    ];
    return lines.join("\n");
  };

  return (
    <section className="section">
      <div className="section-head">
        <h2>API 연결 테스트</h2>
        <span className="sub">서버 구성요소 응답 + 이 브라우저에서 공개 API 로 실제 요청 (외부 원천 API 는 한도 보호를 위해 호출하지 않음)</span>
      </div>
      <div className="toolbar">
        <input className="text-input" style={{ minWidth: 260 }} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
               placeholder="내 API 키로 테스트 (선택) al_live_…" aria-label="테스트에 쓸 API 키 (메모리에만 보관)" />
        <button type="button" className="btn primary" onClick={run} disabled={running}>{running ? "테스트 중…" : "테스트 실행"}</button>
        {results.length > 0 && !running && (
          <>
            <Badge tone={passed === results.length ? "good" : "bad"}>{passed === results.length ? "✓" : "✕"} {passed}/{results.length} 통과</Badge>
            <span className="muted small">지연 중앙값 {med == null ? DASH : `${num(med)}ms`} · {version ?? ""} · {kst(at)}</span>
            <CopyButton text={report} label="결과 복사" className="btn" />
          </>
        )}
      </div>
      {connErr && <div className="error" role="alert"><span>{connErr}</span></div>}
      {(conn || results.length > 0) && (
        <div className="grid-2">
          <div>
            <h3 className="h3">서버 구성요소 <span className="muted small">API 서버 안에서 측정</span></h3>
            {conn ? (
              <DataTable rows={conn.items} rowKey={(i) => i.key} columns={[
                { key: "n", header: "구성요소", cell: (i) => <span className="name">{i.name}<span className="sub">{i.role}</span></span> },
                { key: "s", header: "상태", cell: (i) => (i.ok ? <Badge tone="good">정상</Badge> : <Badge tone={i.key === "dagster" ? "warn" : "bad"}>{i.key === "dagster" ? "꺼짐" : "실패"}</Badge>) },
                { key: "l", header: "지연", align: "right", cell: (i) => (i.latencyMs == null ? DASH : `${num(i.latencyMs, 1)}ms`) },
                { key: "d", header: "비고", cell: (i) => <span className="muted small">{i.note ?? i.error ?? ""}</span> },
              ]} />
            ) : null}
            {conn && (
              <>
                <h3 className="h3">외부 원천 <span className="muted small">직접 호출하지 않고 수집 기록으로 판단</span></h3>
                <div className="stat-list one">
                  {conn.sources.map((s) => (
                    <div className="stat-row" key={s.key}>
                      <span className="k">{s.name}<span className="sub">{s.detail ?? ""}</span></span>
                      <span className="v">{relative(s.lastSuccessAt)}<span className="sub">{s.state}</span></span>
                    </div>
                  ))}
                </div>
                <p className="note">{conn.notes.join(" ")}</p>
              </>
            )}
          </div>
          <div>
            <h3 className="h3">공개 API <span className="muted small">브라우저 → 웹 서버(BFF) → API, 캐시 없이</span></h3>
            <DataTable rows={results} rowKey={(r) => r.id} columns={[
              { key: "n", header: "점검", cell: (r) => <span className="name">{r.name}<span className="sub"><code>{r.path}</code></span></span> },
              { key: "s", header: "결과", cell: (r) => (r.pass ? <Badge tone="good">{r.status}</Badge> : <Badge tone="bad" title={r.reason}>{r.status ?? "실패"}</Badge>) },
              { key: "l", header: "지연", align: "right", cell: (r) => (r.ms == null ? DASH : `${num(r.ms)}ms`) },
              { key: "d", header: "확인", cell: (r) => (
                <span className="muted small">{r.pass ? r.expect : r.reason}{r.cache ? ` · 캐시 ${r.cache}` : ""}{r.remaining ? ` · 남은 요청 ${r.remaining}/${r.limit}` : ""}</span>
              ) },
            ]} />
          </div>
        </div>
      )}
      {!results.length && !running && !connErr && (
        <p className="note">버튼을 누르면 약 10개의 요청을 순서대로 보냅니다 (분당 요청 한도에 포함). 키를 넣으면 그 키의 플랜·한도로 테스트하고, 키는 이 화면 메모리에만 있습니다.</p>
      )}
    </section>
  );
}
