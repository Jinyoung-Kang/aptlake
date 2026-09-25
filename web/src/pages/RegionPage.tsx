import { useEffect, useMemo, useState } from "react";
import { addMonths, api, errorText, ym, type Region } from "../api";
import { baseOption, Chart, cssVar } from "../Chart";
import RegionSelect from "./RegionSelect";

type Month = {
  dealYm: string; reported: number; trades: number; cancelled: number; sampleSize: number;
  p25PricePerM2: number | null; medianPricePerM2: number | null; p75PricePerM2: number | null;
  provisional?: boolean; lowSample?: boolean; missing?: boolean;
};
type Resp = { region: { fullName: string }; items: Month[]; notes: string[]; disclaimer: string; datasetVersion: string };

export default function RegionPage({ regions }: { regions: Region[] }) {
  const now = new Date();
  const [sgg, setSgg] = useState("41135");
  const [to, setTo] = useState(ym(addMonths(now, 0)));
  const [from, setFrom] = useState(ym(addMonths(now, -11)));
  const [data, setData] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    api<Resp>(`/v1/regions/${sgg}/months?from=${from}&to=${to}`).then(setData).catch((e) => { setData(null); setErr(errorText(e)); });
  }, [sgg, from, to]);

  const items = data?.items ?? [];
  // 요청 범위의 모든 달을 축에 두고, 응답에 없는 달(미수집 또는 거래 없음)은 빈칸으로 둔다
  const months = useMemo(() => {
    const byYm = new Map(items.map((m) => [m.dealYm, m]));
    const out: Month[] = [];
    for (let d = new Date(+from.slice(0, 4), +from.slice(5) - 1, 1); ym(d) <= to; d = addMonths(d, 1)) {
      out.push(byYm.get(ym(d)) ?? ({ dealYm: ym(d), reported: 0, trades: null, cancelled: null, sampleSize: 0,
        p25PricePerM2: null, medianPricePerM2: null, p75PricePerM2: null, missing: true } as unknown as Month));
    }
    return out;
  }, [items, from, to]);
  const provisionalRange = useMemo(() => {
    const p = months.filter((m) => m.provisional);
    return p.length ? [[{ xAxis: p[0].dealYm }, { xAxis: p[p.length - 1].dealYm }]] : [];
  }, [months]);

  const volume = useMemo(() => {
    const b = baseOption() as any;
    return {
      ...b,
      legend: { ...b.legend, data: ["거래(해제 제외)", "해제"] },
      xAxis: { ...b.xAxis, type: "category", data: months.map((m) => m.dealYm) },
      yAxis: { ...b.yAxis, type: "value", name: "건", nameTextStyle: { color: cssVar("--text-muted") } },
      series: [
        { name: "거래(해제 제외)", type: "bar", stack: "n", data: months.map((m) => m.trades), barMaxWidth: 28,
          itemStyle: { color: cssVar("--series-1"), borderColor: cssVar("--surface-1"), borderWidth: 1 },
          markArea: { silent: true, itemStyle: { color: cssVar("--grid"), opacity: 0.5 },
                      label: { show: true, position: "insideTop", color: cssVar("--text-muted"), formatter: "잠정" }, data: provisionalRange } },
        { name: "해제", type: "bar", stack: "n", data: months.map((m) => m.cancelled), barMaxWidth: 28,
          itemStyle: { color: cssVar("--series-2"), borderRadius: [4, 4, 0, 0], borderColor: cssVar("--surface-1"), borderWidth: 1 } },
      ],
    };
  }, [months, provisionalRange]);

  const price = useMemo(() => {
    const b = baseOption() as any;
    const lo = months.map((m) => m.p25PricePerM2);
    const width = months.map((m) => (m.p25PricePerM2 != null && m.p75PricePerM2 != null ? m.p75PricePerM2 - m.p25PricePerM2 : null));
    return {
      ...b,
      legend: { ...b.legend, data: ["중위수", "p25–p75"] },
      tooltip: { ...b.tooltip, formatter: (ps: any[]) => {
        const m = months[ps[0].dataIndex];
        return `${m.dealYm}${m.provisional ? " (잠정)" : ""}<br/>중위수 ${m.medianPricePerM2 ?? "–"} 만원/㎡<br/>p25–p75 ${m.p25PricePerM2 ?? "–"} – ${m.p75PricePerM2 ?? "–"}<br/>표본 ${m.sampleSize}건`;
      } },
      xAxis: { ...b.xAxis, type: "category", data: months.map((m) => m.dealYm), boundaryGap: false },
      yAxis: { ...b.yAxis, type: "value", name: "만원/㎡", scale: true, nameTextStyle: { color: cssVar("--text-muted") } },
      series: [
        { name: "p25 base", type: "line", stack: "band", data: lo, lineStyle: { opacity: 0 }, symbol: "none", tooltip: { show: false } },
        { name: "p25–p75", type: "line", stack: "band", data: width, lineStyle: { opacity: 0 }, symbol: "none",
          areaStyle: { color: cssVar("--series-1-band") }, itemStyle: { color: cssVar("--series-1-band") } },
        { name: "중위수", type: "line", data: months.map((m) => m.medianPricePerM2), symbolSize: 8,
          lineStyle: { width: 2, color: cssVar("--series-1") }, itemStyle: { color: cssVar("--series-1"), borderColor: cssVar("--surface-1"), borderWidth: 2 },
          markArea: { silent: true, itemStyle: { color: cssVar("--grid"), opacity: 0.5 }, data: provisionalRange } },
      ],
    };
  }, [months, provisionalRange]);

  return (
    <>
      <div className="card">
        <div className="row">
          <RegionSelect regions={regions} value={sgg} onChange={setSgg} />
          <label>시작 <input type="month" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>끝 <input type="month" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <span className="note">익명 플랜은 최대 12개월 — 더 긴 기간은 API 키로 조회하세요.</span>
        </div>
        {err && <p className="error">{err}</p>}
      </div>
      {data && (
        <>
          <div className="card">
            <h2>{data.region.fullName} · 월별 거래량</h2>
            <p className="sub">계약월 기준 · 해제 거래는 따로 표시 · 회색 음영 = 신고가 아직 추가될 수 있는 잠정 구간 · 빈 달 = 아직 수집 전이거나 거래 없음 (데이터 품질 탭에서 확인)</p>
            <Chart option={volume} label="월별 거래량과 해제 건수 막대 그래프" />
          </div>
          <div className="card">
            <h2>㎡당 가격 분위수</h2>
            <p className="sub">해제·이상치(월 전국 상하위 0.1%) 제외 · 표본 5건 미만 월은 비움</p>
            <Chart option={price} label="㎡당 가격 중위수와 사분위 범위" />
          </div>
          <div className="card scroll">
            <h2>표로 보기</h2>
            <table>
              <thead><tr><th>계약월</th><th className="num">신고</th><th className="num">거래</th><th className="num">해제</th>
                <th className="num">p25</th><th className="num">중위수</th><th className="num">p75</th><th>비고</th></tr></thead>
              <tbody>
                {months.filter((m) => !m.missing).map((m) => (
                  <tr key={m.dealYm}>
                    <td>{m.dealYm}</td><td className="num">{m.reported}</td><td className="num">{m.trades}</td><td className="num">{m.cancelled}</td>
                    <td className="num">{m.p25PricePerM2 ?? "–"}</td><td className="num">{m.medianPricePerM2 ?? "–"}</td><td className="num">{m.p75PricePerM2 ?? "–"}</td>
                    <td>{m.provisional && <span className="badge">잠정</span>} {m.lowSample && <span className="badge">표본 부족</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">{data.notes.join(" ")} {data.disclaimer}</p>
          </div>
        </>
      )}
    </>
  );
}
