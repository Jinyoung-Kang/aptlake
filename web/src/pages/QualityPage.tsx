import { useEffect, useMemo, useState } from "react";
import { addMonths, api, errorText, ym, type Region } from "../api";
import { baseOption, Chart, cssVar } from "../Chart";

type Summary = {
  partitions: Record<string, number>;
  freshness: { lastFetchedAt: string | null; lastChangedAt: string | null };
  dataset: { version: string; publishedAt: string; dataAsOf: string } | null;
  failedChecks7d: { asset: string; partition: string | null; check: string; severity: string; blocking: boolean; metric: unknown; at: string }[];
  apiBudget: { day: string; limit: number; used: number; byPriority: Record<string, number> }[];
};
type Grid = { cells: Record<string, Record<string, [string, number | null]>> };

// 상태 색은 고정 상태 팔레트 + 텍스트 라벨(범례·툴팁)과 함께 쓴다 — 색만으로 구분하지 않음
const STATUS: [string, string, string][] = [
  ["MERGED", "반영 완료", "--good"], ["LOADED", "적재·반영 대기", "--seq-2"], ["PENDING", "미수집", "--neutral"],
  ["FETCHING", "수집 중", "--seq-2"], ["RETRY", "재시도 대기", "--warning"], ["QUARANTINED", "격리", "--critical"],
];

export default function QualityPage({ regions }: { regions: Region[] }) {
  const [s, setS] = useState<Summary | null>(null);
  const [g, setG] = useState<Grid | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const now = new Date();
  const [from] = useState(ym(addMonths(now, -35)));
  const [to] = useState(ym(now));
  useEffect(() => {
    api<Summary>("/v1/quality/summary").then(setS).catch((e) => setErr(errorText(e)));
    api<Grid>(`/v1/quality/partitions?from=${from}&to=${to}`).then(setG).catch((e) => setErr(errorText(e)));
  }, [from, to]);

  const names = useMemo(() => new Map(regions.map((r) => [r.sggCd, r.fullName])), [regions]);
  const heat = useMemo(() => {
    if (!g) return {};
    const sggs = Object.keys(g.cells).sort();
    const months: string[] = [];
    for (let d = new Date(+from.slice(0, 4), +from.slice(5) - 1, 1); ym(d) <= to; d = addMonths(d, 1)) months.push(ym(d).replace("-", ""));
    const idx = new Map(STATUS.map(([k], i) => [k, i]));
    const data: [number, number, number, number | null][] = [];
    sggs.forEach((sgg, y) => months.forEach((m, x) => {
      const c = g.cells[sgg][m];
      if (c) data.push([x, y, idx.get(c[0]) ?? 2, c[1]]);
    }));
    const b = baseOption() as any;
    return {
      ...b,
      grid: { left: 56, right: 16, top: 16, bottom: 72 },
      tooltip: { trigger: "item", backgroundColor: cssVar("--surface-1"), borderColor: cssVar("--border"), textStyle: { color: cssVar("--text-primary") },
        formatter: (p: any) => `${names.get(sggs[p.value[1]]) ?? sggs[p.value[1]]} (${sggs[p.value[1]]})<br/>${months[p.value[0]]} · ${STATUS[p.value[2]][1]}<br/>${p.value[3] ?? "–"}건` },
      xAxis: { ...b.xAxis, type: "category", data: months, splitArea: { show: false } },
      yAxis: { type: "category", data: sggs, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { show: false }, name: `시군구 ${sggs.length}개` },
      visualMap: { type: "piecewise", dimension: 2, orient: "horizontal", left: "center", bottom: 0, textStyle: { color: cssVar("--text-secondary") },
        pieces: STATUS.map(([, label, v], i) => ({ value: i, label, color: cssVar(v) })) },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: cssVar("--surface-1"), borderWidth: 1 }, progressive: 0 }],
    };
  }, [g, names, from, to]);

  const fmt = (t: string | null | undefined) => (t ? new Date(t).toLocaleString("ko-KR") : "–");
  const total = s ? Object.values(s.partitions).reduce((a, b) => a + b, 0) : 0;
  const today = s?.apiBudget[0];
  return (
    <>
      {err && <p className="error">{err}</p>}
      {s && (
        <div className="tiles">
          <div className="tile"><div className="k">최근 원천 수집</div><div className="v" style={{ fontSize: 16 }}>{fmt(s.freshness.lastFetchedAt)}</div>
            <div className="d">마지막 내용 변경 {fmt(s.freshness.lastChangedAt)}</div></div>
          <div className="tile"><div className="k">데이터셋 버전</div><div className="v" style={{ fontSize: 16 }}>{s.dataset?.version ?? "–"}</div>
            <div className="d">발행 {fmt(s.dataset?.publishedAt)}</div></div>
          <div className="tile"><div className="k">반영 완료 파티션</div><div className="v">{(s.partitions.MERGED ?? 0).toLocaleString()}</div>
            <div className="d">전체 {total.toLocaleString()} · 격리 {s.partitions.QUARANTINED ?? 0} · 재시도 {s.partitions.RETRY ?? 0}</div></div>
          <div className="tile"><div className="k">오늘 원천 호출 예산</div><div className="v">{today ? `${today.used.toLocaleString()}` : "–"}</div>
            <div className="d">{today ? `일 한도 ${today.limit.toLocaleString()} 중 (상한 80%)` : "기록 없음"}</div></div>
        </div>
      )}
      <div className="card">
        <h2>파티션 상태 (시군구 × 계약월)</h2>
        <p className="sub">{from} ~ {to} · 칸에 마우스를 올리면 시군구·건수 · 격리 파티션은 관리 API 로 재시도</p>
        <Chart option={heat} height={420} label="시군구와 계약월별 수집 상태 히트맵" />
      </div>
      <div className="card scroll">
        <h2>최근 7일 실패한 품질 검사</h2>
        {s && s.failedChecks7d.length === 0 && <p className="note">없음</p>}
        {s && s.failedChecks7d.length > 0 && (
          <table>
            <thead><tr><th>시각</th><th>자산</th><th>파티션</th><th>검사</th><th>등급</th><th>지표</th></tr></thead>
            <tbody>{s.failedChecks7d.map((f, i) => (
              <tr key={i}><td>{fmt(f.at)}</td><td>{f.asset}</td><td>{f.partition}</td><td>{f.check}</td>
                <td><span className="status"><span className="dot" style={{ background: `var(${f.blocking ? "--critical" : "--warning"})` }} />{f.blocking ? "차단" : "경고"}</span></td>
                <td><code>{JSON.stringify(f.metric).slice(0, 80)}</code></td></tr>))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}
