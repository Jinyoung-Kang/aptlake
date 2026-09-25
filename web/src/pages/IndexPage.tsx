import { useEffect, useMemo, useState } from "react";
import { api, errorText, type Region } from "../api";
import { baseOption, Chart, cssVar } from "../Chart";

type Point = { period: string; value: number; ciLow: number; ciHigh: number; nObs: number; provisional?: boolean };
type Resp = {
  regionId: string; method: string; base: string; series: Point[];
  reference: { source: string | null; series: { period: string; value: number }[] };
  validation: { reference: string; corrMoM: number | null; directionMatch: number | null; months: number; window: string } | null;
  disclaimer: string;
};

export default function IndexPage({ regions }: { regions: Region[] }) {
  const sidos = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of regions) m.set(r.sidoCd, r.sidoName);
    return [["00", "전국"], ...[...m.entries()].sort()];
  }, [regions]);
  const [rid, setRid] = useState("00");
  const [d, setD] = useState<Resp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setErr(null);
    api<Resp>(`/v1/index?regionId=${rid}`).then(setD).catch((e) => { setD(null); setErr(errorText(e)); });
  }, [rid]);

  // 두 지수는 기준 시점이 다르므로 (자체: 첫 달=100, R-ONE: 2026.06=100) 겹치는 첫 달을 공통 기준(=100)으로 다시 맞춘다 — 한 축만 사용
  const view = useMemo(() => {
    if (!d) return null;
    const ref = new Map(d.reference.series.map((r) => [r.period, r.value]));
    const own = new Map(d.series.map((p) => [p.period, p]));
    // 달이 빠진 구간은 null 로 채워 연속 월 축에서 끊어 그린다 (빠진 달을 이어 붙이지 않음)
    const periods: string[] = [];
    if (d.series.length) {
      const [y0, m0] = d.series[0].period.split("-").map(Number);
      const last = d.series[d.series.length - 1].period;
      for (let i = 0; ; i++) {
        const dt = new Date(y0, m0 - 1 + i, 1);
        const p = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`;
        periods.push(p);
        if (p === last) break;
      }
    }
    const base = d.series.find((p) => ref.has(p.period));
    const k = base ? 100 / base.value : 1, kr = base ? 100 / (ref.get(base.period) as number) : 1;
    return {
      base: base?.period ?? null,
      rows: periods.map((period) => {
        const p = own.get(period);
        const r = ref.get(period);
        return { period, value: p ? p.value * k : null, ciLow: p ? p.ciLow * k : null, ciHigh: p ? p.ciHigh * k : null,
                 ref: base && r != null ? r * kr : null };
      }),
    };
  }, [d]);

  const option = useMemo(() => {
    if (!view) return {};
    const b = baseOption() as any;
    const rows = view.rows;
    return {
      ...b,
      legend: { ...b.legend, data: ["자체 지수 (HEDONIC_TD_v1)", "95% 신뢰구간", "R-ONE 매매지수_아파트"] },
      xAxis: { ...b.xAxis, type: "category", data: rows.map((r) => r.period), boundaryGap: false },
      yAxis: { ...b.yAxis, type: "value", scale: true, name: view.base ? `${view.base}=100` : "", nameTextStyle: { color: cssVar("--text-muted") } },
      series: [
        { name: "ci base", type: "line", stack: "ci", data: rows.map((r) => r.ciLow), symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false } },
        { name: "95% 신뢰구간", type: "line", stack: "ci", data: rows.map((r) => (r.ciHigh == null || r.ciLow == null ? null : r.ciHigh - r.ciLow)), symbol: "none",
          lineStyle: { opacity: 0 }, areaStyle: { color: cssVar("--series-1-band") }, itemStyle: { color: cssVar("--series-1-band") }, tooltip: { show: false } },
        { name: "자체 지수 (HEDONIC_TD_v1)", type: "line", data: rows.map((r) => (r.value == null ? null : +r.value.toFixed(2))), symbol: "none",
          lineStyle: { width: 2, color: cssVar("--series-1") }, itemStyle: { color: cssVar("--series-1") } },
        { name: "R-ONE 매매지수_아파트", type: "line", data: rows.map((r) => (r.ref == null ? null : +r.ref.toFixed(2))), symbol: "none",
          lineStyle: { width: 2, color: cssVar("--series-2") }, itemStyle: { color: cssVar("--series-2") } },
      ],
    };
  }, [view]);

  const pct = (v: number | null | undefined) => (v == null ? "–" : `${(v * 100).toFixed(0)}%`);
  return (
    <>
      <div className="card">
        <div className="row">
          <label>지역
            <select value={rid} onChange={(e) => setRid(e.target.value)}>
              {sidos.map(([c, n]) => <option key={c} value={c}>{n} ({c})</option>)}
            </select>
          </label>
        </div>
        {err && <p className="error">{err}</p>}
      </div>
      {d && view && (
        <>
          <div className="tiles">
            <div className="tile"><div className="k">월간 변화율 상관 (vs R-ONE)</div><div className="v">{d.validation?.corrMoM?.toFixed(2) ?? "–"}</div>
              <div className="d">{d.validation?.window ?? "겹치는 기간 부족"}</div></div>
            <div className="tile"><div className="k">방향 일치율</div><div className="v">{pct(d.validation?.directionMatch)}</div>
              <div className="d">{d.validation?.months ?? 0}개월 비교</div></div>
            <div className="tile"><div className="k">최근 월 관측 수</div><div className="v">{d.series.at(-1)?.nObs.toLocaleString() ?? "–"}</div>
              <div className="d">{d.series.at(-1)?.period}{d.series.at(-1)?.provisional ? " (잠정)" : ""}</div></div>
          </div>
          <div className="card">
            <h2>자체 지수 vs R-ONE</h2>
            <p className="sub">log(㎡당 가격) = 단지 고정효과 + 월 더미 + 층·면적 구간 더미 · 음영 = 95% 신뢰구간(단지 군집 강건) · 두 지수를 {view.base ?? "-"}=100 으로 맞춤 · 수집 완결(시군구 90% 이상) 월만 산출, 빈 구간은 끊어 표시</p>
            <Chart option={option} height={360} label="자체 가격지수와 R-ONE 지수 비교 선 그래프" />
            <p className="note">기준 지수: {d.reference.source ?? "없음"} · {d.disclaimer}</p>
          </div>
        </>
      )}
    </>
  );
}
