import { useEffect, useState } from "react";
import { api, errorText, type Region } from "../api";
import RegionSelect from "./RegionSelect";

type Trade = {
  tradeId: string; dealDate: string; priceManwon: number; areaM2: number; floor: number | null; pricePerM2: number | null;
  cancelled: boolean; registeredDate: string | null; dealKind: string | null; version: number; outlier: boolean;
  missingSince: string | null; complex: { complexKey: string; aptName: string; umdName: string; buildYear: number | null };
};
type Page = { items: Trade[]; page: { nextCursor: string | null } };
type Version = { version: number; validFrom: string; validTo: string | null; current: boolean;
  changes: { field: string; from: unknown; to: unknown }[]; state: Record<string, unknown> };

const FIELD: Record<string, string> = { cancelled: "해제", cancelDate: "해제일", registeredDate: "등기일", aptDong: "동",
  dealKind: "거래유형", sellerType: "매도자", buyerType: "매수자" };

function lastMonthRange() {
  const d = new Date();
  const s = new Date(d.getFullYear(), d.getMonth() - 1, 1);
  const e = new Date(d.getFullYear(), d.getMonth(), 0);
  const f = (x: Date) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  return [f(s), f(e)];
}

export default function TradesPage({ regions }: { regions: Region[] }) {
  const [d0, d1] = lastMonthRange();
  const [sgg, setSgg] = useState("41135");
  const [from, setFrom] = useState(d0);
  const [to, setTo] = useState(d1);
  const [inclCancel, setInclCancel] = useState(true);
  const [rows, setRows] = useState<Trade[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<Trade | null>(null);
  const [hist, setHist] = useState<Version[] | null>(null);

  const q = `/v1/trades?sggCd=${sgg}&from=${from}&to=${to}&includeCancelled=${inclCancel}&limit=100`;
  useEffect(() => {
    setErr(null); setSel(null); setHist(null);
    api<Page>(q).then((p) => { setRows(p.items); setCursor(p.page.nextCursor); }).catch((e) => { setRows([]); setCursor(null); setErr(errorText(e)); });
  }, [q]);

  const more = () => cursor && api<Page>(`${q}&cursor=${encodeURIComponent(cursor)}`)
    .then((p) => { setRows((r) => [...r, ...p.items]); setCursor(p.page.nextCursor); })
    .catch((e) => setErr(errorText(e)));

  const open = (t: Trade) => {
    setSel(t); setHist(null);
    api<{ versions: Version[] }>(`/v1/trades/${t.tradeId}/history`).then((h) => setHist(h.versions)).catch((e) => setErr(errorText(e)));
  };

  return (
    <>
      <div className="card">
        <div className="row">
          <RegionSelect regions={regions} value={sgg} onChange={setSgg} />
          <label>계약일 from <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>to <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <label style={{ flexDirection: "row", alignItems: "center" }}>
            <input type="checkbox" style={{ minWidth: 0 }} checked={inclCancel} onChange={(e) => setInclCancel(e.target.checked)} /> 해제 거래 포함
          </label>
        </div>
        {err && <p className="error">{err}</p>}
      </div>
      <div className="split">
        <div className="card scroll">
          <h2>거래 {rows.length}건{cursor ? "+" : ""}</h2>
          <p className="sub">최신 계약일 순 · 행을 누르면 버전 이력 (해제·등기 변경)</p>
          <table>
            <thead><tr><th>계약일</th><th>단지</th><th className="num">전용㎡</th><th className="num">층</th><th className="num">거래금액(만원)</th><th>상태</th></tr></thead>
            <tbody>
              {rows.map((t) => (
                <tr key={t.tradeId} className={`clickable ${sel?.tradeId === t.tradeId ? "selected" : ""}`} onClick={() => open(t)}>
                  <td>{t.dealDate}</td>
                  <td>{t.complex.aptName} <span className="note">{t.complex.umdName}</span></td>
                  <td className="num">{t.areaM2.toFixed(2)}</td>
                  <td className="num">{t.floor ?? "–"}</td>
                  <td className="num">{t.priceManwon.toLocaleString()}</td>
                  <td>
                    {t.cancelled && <span className="badge">해제</span>} {t.version > 1 && <span className="badge">v{t.version}</span>}{" "}
                    {t.outlier && <span className="badge">이상치</span>} {t.missingSince && <span className="badge">원천 미관측</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {cursor && <p><button className="ghost" onClick={more}>더 보기</button></p>}
        </div>
        <div className="card">
          <h2>버전 이력</h2>
          {!sel && <p className="note">왼쪽 표에서 거래를 선택하세요.</p>}
          {sel && (
            <>
              <p className="sub">{sel.complex.aptName} · {sel.dealDate} · {sel.priceManwon.toLocaleString()}만원<br /><code>{sel.tradeId}</code></p>
              {!hist && <p className="note">불러오는 중…</p>}
              <ul className="timeline">
                {hist?.map((v) => (
                  <li key={v.version}>
                    <strong>v{v.version}</strong> {v.current && <span className="badge">현재</span>}
                    <div className="note">{new Date(v.validFrom).toLocaleString("ko-KR")} 부터{v.validTo ? ` ~ ${new Date(v.validTo).toLocaleString("ko-KR")}` : ""}</div>
                    {v.changes.length === 0 && v.version === 1 && <div className="note">최초 관측</div>}
                    {v.changes.map((c) => (
                      <div key={c.field}>{FIELD[c.field] ?? c.field}: {String(c.from ?? "없음")} → <strong>{String(c.to ?? "없음")}</strong></div>
                    ))}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>
    </>
  );
}
