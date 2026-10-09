import { useState } from "react";
import { api, errorText } from "../api/client";
import { axisX, axisY, base, Chart, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { CopyButton, ErrorBox, Kpi, sqm } from "../components/ui";
import { esc, num } from "../lib/format";
import { paths } from "../api/endpoints";
import type { MyUsage, MyUsageMonthly, Plans } from "../api/types";
import { useApi } from "../hooks/useApi";
import { planRow, todayPct } from "../domain/developer";

const BASE = "http://127.0.0.1:8610";
const EXAMPLES: { title: string; desc: string; path: string }[] = [
  { title: "시군구 월별 통계", desc: "거래·해제·m²당 가격 분위수 (플랜 기간 상한 적용)", path: "/v1/regions/41135/months?from=2025-07&to=2026-06" },
  { title: "거래 목록 (커서 페이지)", desc: "응답의 page.nextCursor 를 cursor 로 넘기면 다음 페이지. 서명된 값이라 조건을 바꾸면 400", path: "/v1/trades?sggCd=41135&from=2026-06-01&to=2026-06-30&limit=200" },
  { title: "거래 버전 이력", desc: "해제·등기일 등 원천 값이 바뀐 기록 (SCD2)", path: "/v1/trades/{tradeId}/history" },
  { title: "시장 개요", desc: "전국·시도·시군구 한 달 요약과 순위", path: "/v1/market/overview?ym=2026-06" },
  { title: "자체 지수 + 검증", desc: "HEDONIC_TD_v1 시계열, R-ONE 대비 상관·방향 일치율", path: "/v1/index?regionId=11" },
  { title: "파티션 품질·계보", desc: "검사 결과, 원본 XML 경로, Iceberg 스냅샷, 발행 버전", path: "/v1/quality/partitions/41135/2026-06" },
];

export default function DeveloperPage() {
  // 키는 이 화면 state 에만 (저장소·URL 에 쓰지 않음, 탭을 닫으면 사라짐)
  const [key, setKey] = useState("");
  const [usage, setUsage] = useState<MyUsage | null>(null);
  const [monthly, setMonthly] = useState<MyUsageMonthly | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const plans = useApi<Plans>(paths.plans());
  const load = () => {
    setErr(null);
    const k = { key: key.trim() };
    Promise.all([api<MyUsage>(paths.myUsage(30), k), api<MyUsageMonthly>(paths.myUsageMonthly(6), k)])
      .then(([u, m]) => { setUsage(u); setMonthly(m); })
      .catch((e) => { setUsage(null); setMonthly(null); setErr(errorText(e)); });
  };
  const build = (p: Palette) => {
    const b = base(p);
    const it = usage?.items ?? [];
    return {
      ...b, legend: { show: false },
      // 기본 툴팁은 style 속성이 든 HTML 을 만든다 → CSP(인라인 스타일 금지)에 맞춰 직접 구성
      tooltip: { ...b.tooltip, formatter: (ps: { dataIndex: number }[]) => {
        const i = it[ps[0].dataIndex];
        return `<b>${esc(i.day)}</b><br/>요청 <b>${num(i.requests)}</b> · 오류 ${num(i.errors)} · p95 ${num(i.p95Ms)}ms`;
      } },
      xAxis: axisX(p, { type: "category", data: it.map((i) => i.day.slice(5)) }),
      yAxis: axisY(p, { type: "value", name: "요청", minInterval: 1 }),
      series: [{ type: "bar", name: "요청", data: it.map((i) => i.requests), barMaxWidth: 22, itemStyle: { color: p.s1, borderRadius: [3, 3, 0, 0] } }],
    };
  };
  return (
    <>
      <div className="page-head"><div><div className="crumb">개발자</div><h1>데이터 API</h1></div>
        <div className="tools"><a className="btn primary" href="/docs" target="_blank" rel="noreferrer">OpenAPI 문서 열기 ↗</a></div></div>
      <div className="layout">
        <div>
          <section className="section">
            <div className="section-head"><h2>인증</h2></div>
            <p style={{ marginTop: 0 }}>요청 헤더에 키를 넣습니다. 키는 발급할 때 한 번만 보이고, 서버는 원문 대신 HMAC 만 저장합니다.</p>
            <div className="code-block"><pre /* biome-ignore lint/a11y/noNoninteractiveTabindex: 스크롤 영역은 키보드로도 넘길 수 있어야 한다 (WCAG 2.1.1, axe scrollable-region-focusable, QA-011) */ tabIndex={0} className="code">X-API-Key: al_live_&lt;keyId 12자&gt;.&lt;secret&gt;</pre></div>
            <p className="note">키가 없으면 anonymous 플랜(IP 기준)입니다. 한도를 넘으면 429 와 Retry-After, 모든 오류는 RFC 9457 Problem Details(code·traceId 포함).
              응답 헤더 X-Dataset-Version · X-Data-As-Of 로 어떤 발행본인지 알 수 있고, 같은 버전이면 ETag 로 304 를 받습니다.</p>
          </section>
          <section className="section">
            <div className="section-head"><h2>예시</h2><span className="sub">복사해서 터미널에 붙여 넣으세요 ($APTLAKE_KEY 는 직접 설정)</span></div>
            {EXAMPLES.map((e) => {
              const cmd = `curl -s -H "X-API-Key: $APTLAKE_KEY" "${BASE}${e.path}"`;
              return (
                <div key={e.path} style={{ marginBottom: 12 }}>
                  <div style={{ fontWeight: 700 }}>{e.title} <span className="muted small">{sqm(e.desc)}</span></div>
                  <div className="code-block"><pre /* biome-ignore lint/a11y/noNoninteractiveTabindex: 스크롤 영역은 키보드로도 넘길 수 있어야 한다 (WCAG 2.1.1, axe scrollable-region-focusable, QA-011) */ tabIndex={0} className="code">{cmd}</pre><CopyButton text={cmd} className="btn copy" /></div>
                </div>
              );
            })}
          </section>
        </div>
        <aside>
          <section className="section">
            <div className="section-head"><h2>플랜</h2></div>
            <ErrorBox error={plans.error} onRetry={plans.reload} />
            <DataTable rows={(plans.data?.plans ?? []).map(planRow)} rowKey={(r) => r.p} empty={plans.loading ? "불러오는 중…" : "플랜 정보 없음"} columns={[
              { key: "p", header: "플랜", cell: (r) => <span className="name">{r.p}<span className="sub">{r.who}</span></span> },
              { key: "rpm", header: "분당", align: "right", cell: (r) => num(r.rpm) },
              { key: "rows", header: "일일 행", align: "right", cell: (r) => num(r.rows) },
              { key: "range", header: "기간", cell: (r) => r.range },
              { key: "bulk", header: "대량", cell: (r) => r.bulk },
            ]} />
            <p className="note">일일 '행' 한도는 거래 단위 레코드(거래 목록·이력·산점도·변경분 피드)에만 적용되고, 집계 응답(월별 통계·지도·순위)은 요청 수 한도만 받습니다.
              웹 화면은 BFF 방식으로, 브라우저에는 키가 없고 웹 서버가 서버 쪽 키를 붙입니다.</p>
          </section>
          <section className="section">
            <div className="section-head"><h2>내 키 사용량</h2></div>
            <div className="toolbar">
              <input className="text-input" style={{ flex: 1, minWidth: 180 }} type="password" autoComplete="off" value={key}
                     onChange={(e) => setKey(e.target.value)} placeholder="al_live_…" aria-label="API 키 (메모리에만 보관)" />
              <button type="button" className="btn primary" disabled={!key.trim()} onClick={load}>조회</button>
            </div>
            <ErrorBox error={err} />
            {usage && (
              <>
                <div className="kpis" style={{ gridTemplateColumns: "1fr 1fr" }}>
                  <Kpi k="플랜" v={usage.plan} d={`분당 ${num(usage.limits.rpm)} · 일 ${num(usage.limits.dailyRows)}행 · 페이지 ${num(usage.limits.maxPageSize)}`} />
                  <Kpi k="오늘 남은 행 (이 키)" v={num(usage.today.rowsLeft)}
                       d={<>사용 {num(usage.today.rowsUsed)}행 · 한국 날짜 기준<div className="progress"><span style={{ width: `${todayPct(usage)}%` }} /></div></>} />
                </div>
                <div className="panel panel-pad"><Chart build={build} deps={[usage]} height={200} label="일별 요청 수 (이 고객의 모든 키)" /></div>
                {monthly && (
                  <>
                    <h3 style={{ fontSize: 13, margin: "12px 0 6px" }}>달별 과금 대상 단위 (이 고객의 모든 키)</h3>
                    <DataTable rows={monthly.items} rowKey={(m) => m.month} empty="최근 6개월 사용 기록이 없습니다." columns={[
                      { key: "m", header: "달", cell: (m) => m.month },
                      { key: "r", header: "요청", align: "right", cell: (m) => num(m.requests) },
                      { key: "c", header: "차감 행", align: "right", cell: (m) => num(m.chargedRows) },
                      { key: "x", header: "내보내기 행", align: "right", cell: (m) => `${num(m.exportRows)} (${num(m.exports)}건)` },
                      { key: "l", header: "한도 초과", align: "right", cell: (m) => num(m.rateLimited) },
                    ]} />
                    <p className="note">공개 단가가 없는 학습용 서비스라 금액 대신 과금 대상이 될 단위만 보입니다. 차감 행 = 거래 단위 레코드(거래 목록·이력·산점도·변경분 피드) 수.</p>
                  </>
                )}
              </>
            )}
            <p className="note">키는 이 화면 메모리에만 있고 저장하지 않습니다.</p>
          </section>
        </aside>
      </div>
    </>
  );
}
