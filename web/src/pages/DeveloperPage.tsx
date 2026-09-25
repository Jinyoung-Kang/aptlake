import { useMemo, useState } from "react";
import { api, errorText } from "../api";
import { baseOption, Chart, cssVar } from "../Chart";

type Usage = { clientId: string; plan: string; limits: { rpm: number; dailyRows: number };
  items: { day: string; requests: number; rows: number; errors: number; p95Ms: number }[] };

export default function DeveloperPage() {
  // 키는 이 컴포넌트 state 에만 있고 localStorage·URL 에 쓰지 않는다 (탭을 닫으면 사라짐)
  const [key, setKey] = useState("");
  const [u, setU] = useState<Usage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => { setErr(null); api<Usage>("/v1/me/usage?days=30", key.trim()).then(setU).catch((e) => { setU(null); setErr(errorText(e)); }); };
  const option = useMemo(() => {
    if (!u) return {};
    const b = baseOption() as any;
    return { ...b, xAxis: { ...b.xAxis, type: "category", data: u.items.map((i) => i.day) },
      yAxis: { ...b.yAxis, type: "value", name: "요청", nameTextStyle: { color: cssVar("--text-muted") } },
      series: [{ name: "요청", type: "bar", data: u.items.map((i) => i.requests), barMaxWidth: 24,
        itemStyle: { color: cssVar("--series-1"), borderRadius: [4, 4, 0, 0] } }] };
  }, [u]);
  return (
    <>
      <div className="card">
        <h2>데이터 API</h2>
        <p>문서: <a href="/docs" target="_blank" rel="noreferrer">/docs</a> (OpenAPI) · 인증 헤더 <code>X-API-Key: al_live_&lt;keyId&gt;.&lt;secret&gt;</code></p>
        <table>
          <thead><tr><th>플랜</th><th className="num">분당 요청</th><th className="num">일일 행</th><th className="num">최대 기간</th><th>대량 내려받기</th></tr></thead>
          <tbody>
            <tr><td>anonymous (키 없음, IP 기준)</td><td className="num">20</td><td className="num">5,000</td><td className="num">12개월</td><td>×</td></tr>
            <tr><td>free</td><td className="num">60</td><td className="num">50,000</td><td className="num">24개월</td><td>×</td></tr>
            <tr><td>pro</td><td className="num">600</td><td className="num">2,000,000</td><td className="num">전체</td><td>Parquet</td></tr>
          </tbody>
        </table>
        <pre style={{ overflowX: "auto" }}><code>{`curl -H "X-API-Key: $APTLAKE_KEY" \\
  "http://127.0.0.1:8610/v1/regions/41135/months?from=2025-01&to=2025-12"`}</code></pre>
      </div>
      <div className="card">
        <h2>내 키 사용량</h2>
        <div className="row">
          <label style={{ flex: 1 }}>API 키 (브라우저 메모리에만 보관)
            <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="al_live_…" /></label>
          <button className="primary" onClick={load} disabled={!key}>조회</button>
        </div>
        {err && <p className="error">{err}</p>}
        {u && (
          <>
            <p className="sub">플랜 {u.plan} · 분당 {u.limits.rpm}회 · 일 {u.limits.dailyRows.toLocaleString()}행</p>
            <Chart option={option} height={240} label="일별 API 요청 수 막대 그래프" />
            <table>
              <thead><tr><th>일자</th><th className="num">요청</th><th className="num">행</th><th className="num">오류</th><th className="num">p95 ms</th></tr></thead>
              <tbody>{u.items.map((i) => (<tr key={i.day}><td>{i.day}</td><td className="num">{i.requests}</td><td className="num">{i.rows}</td>
                <td className="num">{i.errors}</td><td className="num">{i.p95Ms}</td></tr>))}</tbody>
            </table>
          </>
        )}
      </div>
    </>
  );
}
