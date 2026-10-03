import { useState } from "react";
import { useAvailable } from "../hooks/useApi";
import { DASH, kst, num, relative } from "../lib/format";
import { reportText, summarize } from "../domain/apiProbes";
import { useApiTest } from "../hooks/useApiTest";
import { recentRegions } from "../lib/regions";
import { DataTable } from "./DataTable";
import { Badge, CopyButton } from "./ui";

/**
 * API 연결 테스트 (수집 상태 메뉴).
 *  1) 서버 구성요소: API 서버가 운영 DB·캐시·서빙 DB·오케스트레이터에 닿는지 (/v1/ops/connectivity, 서버 안에서 측정)
 *  2) 공개 API 점검: 이 브라우저 → 웹 서버(BFF) → API 경로로 실제 요청을 보내 상태 코드·지연·헤더 계약을 확인
 * 외부 원천 API 는 호출하지 않는다 (일일 한도 보호) — 서버가 기록한 마지막 성공 수집 시각만 보여 준다.
 */

export default function ApiTest() {
  const avail = useAvailable();
  const [key, setKey] = useState("");  // 메모리에만 (저장·URL 에 쓰지 않음)
  const { running, conn, connErr, results, at, run } = useApiTest(avail, () => recentRegions()[0] ?? "11110");
  const { passed, median: med, version } = summarize(results);
  const report = () => reportText({ at, results, conn, connErr, withKey: !!key.trim() });

  return (
    <section className="section">
      <div className="section-head">
        <h2>API 연결 테스트</h2>
        <span className="sub">서버 구성요소 응답 + 이 브라우저에서 공개 API 로 실제 요청 (외부 원천 API 는 한도 보호를 위해 호출하지 않음)</span>
      </div>
      <div className="toolbar">
        <input className="text-input" style={{ minWidth: 260 }} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
               placeholder="내 API 키로 테스트 (선택) al_live_…" aria-label="테스트에 쓸 API 키 (메모리에만 보관)" />
        <button type="button" className="btn primary" onClick={() => run(key)} disabled={running}>{running ? "테스트 중…" : "테스트 실행"}</button>
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
