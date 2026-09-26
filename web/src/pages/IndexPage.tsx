import { useMemo } from "react";
import { useApi, type IndexSummaryItem } from "../lib/api";
import { axisX, axisY, base, Chart, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { Badge, Change, ErrorBox, Kpi, Segmented, Skeleton, Sparkline, Sqm } from "../components/ui";
import { DASH, num, ymLabel } from "../lib/format";
import { setParams, type Route } from "../lib/router";
import { useRegions } from "../lib/regions";

type Point = { period: string; value: number; ciLow: number; ciHigh: number; nObs: number; provisional?: boolean };
type IndexResp = {
  regionId: string; method: string; base: string; series: Point[];
  reference: { source: string | null; series: { period: string; value: number }[] };
  validation: { reference: string; corrMoM: number | null; directionMatch: number | null; months: number; window: string } | null;
  disclaimer: string;
};
type SummaryItem = IndexSummaryItem;
type Span = "12" | "24" | "36" | "all";

export default function IndexPage({ route }: { route: Route }) {
  const rid = route.params.get("region") ?? "00";
  const span = (route.params.get("span") as Span) || "all";
  const { sidoName } = useRegions();
  const { data, error, loading, stale, reload } = useApi<IndexResp>(`/v1/index?regionId=${rid}`);
  const summary = useApi<{ items: SummaryItem[] }>("/v1/index/summary");
  const regions = summary.data?.items ?? [];
  const cur = regions.find((r) => r.regionId === rid);
  const head = cur ? (cur.confirmed ?? cur) : undefined;

  const view = useMemo(() => {
    if (!data) return null;
    const series = span === "all" ? data.series : data.series.slice(-Number(span));
    const ref = new Map(data.reference.series.map((r) => [r.period, r.value]));
    // 두 지수는 기준 시점이 달라(자체: 첫 달=100, R-ONE: 2026.06=100) 표시 구간에서 겹치는 첫 달을 100 으로 맞춘다 — 축 하나
    const b = series.find((p) => ref.has(p.period));
    const k = b ? 100 / b.value : 1;
    const kr = b ? 100 / (ref.get(b.period) as number) : 1;
    return {
      base: b?.period ?? null,
      rows: series.map((p) => ({ ...p, v: p.value * k, lo: p.ciLow * k, hi: p.ciHigh * k, ref: b && ref.has(p.period) ? (ref.get(p.period) as number) * kr : null })),
    };
  }, [data, span]);

  const build = (p: Palette) => {
    const b = base(p);
    const rows = view?.rows ?? [];
    const prov = rows.filter((r) => r.provisional).map((r) => r.period);
    const provArea = prov.length ? [[{ xAxis: prov[0], name: "잠정" }, { xAxis: prov[prov.length - 1] }]] : [];
    return {
      ...b,
      legend: { ...b.legend, data: ["자체 지수 HEDONIC_TD_v1", "95% 신뢰구간", "R-ONE 매매지수(아파트)"] },
      tooltip: {
        ...b.tooltip,
        formatter: (ps: { dataIndex: number }[]) => {
          const r = rows[ps[0].dataIndex];
          return `<b>${ymLabel(r.period)}</b>${r.provisional ? " (잠정)" : ""}<br/>자체 지수 <b>${num(r.v, 2)}</b> (95% ${num(r.lo, 2)}~${num(r.hi, 2)})` +
            `<br/>R-ONE ${r.ref == null ? DASH : num(r.ref, 2)}<br/><span class="tt-muted">관측 거래 ${num(r.nObs)}건</span>`;
        },
      },
      xAxis: axisX(p, { type: "category", data: rows.map((r) => r.period), boundaryGap: false, axisLabel: { color: p.text3, fontSize: 11, formatter: (v: string) => ymLabel(v) } }),
      yAxis: axisY(p, { type: "value", scale: true, name: view?.base ? `${ymLabel(view.base)} = 100` : "" }),
      series: [
        { name: "ci", type: "line", stack: "ci", data: rows.map((r) => r.lo), symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false }, silent: true },
        { name: "95% 신뢰구간", type: "line", stack: "ci", data: rows.map((r) => r.hi - r.lo), symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: p.band }, itemStyle: { color: p.band } },
        { name: "자체 지수 HEDONIC_TD_v1", type: "line", data: rows.map((r) => +r.v.toFixed(2)), symbol: "none", lineStyle: { width: 2, color: p.s1 }, itemStyle: { color: p.s1 },
          markArea: { silent: true, itemStyle: { color: p.grid, opacity: 0.6 }, label: { show: true, color: p.text3, fontSize: 11, position: "insideTop" }, data: provArea } },
        { name: "R-ONE 매매지수(아파트)", type: "line", data: rows.map((r) => (r.ref == null ? null : +r.ref.toFixed(2))), symbol: "none", lineStyle: { width: 2, color: p.s2 }, itemStyle: { color: p.s2 } },
      ],
    };
  };

  const v = data?.validation;
  const corrText = (c: number | null | undefined) => c == null ? "비교 기간 부족" : c >= 0.8 ? "매우 비슷하게 움직임" : c >= 0.6 ? "대체로 비슷함" : c >= 0.3 ? "약하게 비슷함" : "차이가 큼";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">가격지수 · 자체 산출 실험 지수 (공식 통계 아님)</div>
          <h1>{sidoName(rid)} 아파트 가격지수</h1>
        </div>
      </div>
      <div className="chips" role="group" aria-label="지역" style={{ marginBottom: 14 }}>
        {[{ id: "00" }, ...regions.filter((r) => r.regionId !== "00").map((r) => ({ id: r.regionId }))].map((r) => (
          <button key={r.id} type="button" className="chip" aria-pressed={r.id === rid} onClick={() => setParams(route, { region: r.id })}>{sidoName(r.id)}</button>
        ))}
      </div>
      {cur && head && (
        <div style={{ marginBottom: 14 }}>
          <div className="quote">
            <span className="price">{num(head.value, 2)}</span>
            <span className="chg"><Change v={head.mom} digits={2} title="전월 대비" /></span>
            <span className="muted small">전월 대비</span>
            <span className="chg"><Change v={head.yoy} digits={2} title="전년 같은 달 대비" /></span>
            <span className="muted small">전년 대비</span>
            {head.provisional && <Badge tone="warn">잠정</Badge>}
          </div>
          <div className="quote-sub">
            {ymLabel(head.period)} 기준{cur.confirmed ? " (확정된 최근 달)" : ""} · {data?.base ?? ""} · HEDONIC_TD_v1
            {cur.confirmed && cur.period !== head.period && (
              <> · 잠정 {ymLabel(cur.period)} {num(cur.value, 2)} (<Change v={cur.mom} digits={2} title="잠정 달의 전월 대비" />)</>
            )}
          </div>
        </div>
      )}
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={380} /> : null}
      {data && view && (
        <div className="layout" data-stale={stale} aria-busy={loading}>
          <section className="section">
            <div className="section-head">
              <h2>자체 지수 vs R-ONE</h2>
              <span className="sub">두 지수를 {view.base ? ymLabel(view.base) : "-"}=100 으로 맞춤 · 음영 = 95% 신뢰구간</span>
              <div className="tools">
                <Segmented label="기간" value={span} onChange={(x) => setParams(route, { span: x })}
                           options={[{ value: "12", label: "1년" }, { value: "24", label: "2년" }, { value: "36", label: "3년" }, { value: "all", label: "전체" }]} />
              </div>
            </div>
            <div className="panel panel-pad"><Chart build={build} deps={[view]} height={380} label="자체 가격지수와 R-ONE 지수 비교" /></div>
            <details className="note" style={{ marginTop: 10 }}>
              <summary style={{ cursor: "pointer", color: "var(--text-2)" }}>산출 방법</summary>
              <p>log(<Sqm />당 가격) = 단지 고정효과 + 월 더미 + 층 구간 + 면적 구간 + 오차. 월 계수를 지수로 바꿉니다(첫 달 = 100).
                 같은 단지 안의 가격 변화로 월 효과를 추정하므로, 비싼 단지 거래가 특정 달에 몰려도 지수가 튀지 않습니다.
                 표준오차는 단지 단위 군집 강건 분산, 수집이 완결된 달(시군구 90% 이상 반영)만 씁니다.</p>
              <p>{data.disclaimer}</p>
            </details>
          </section>
          <aside>
            <div className="section-head"><h2>R-ONE 대비 검증</h2></div>
            <div className="kpis" style={{ gridTemplateColumns: "1fr" }}>
              <Kpi k="월간 변화율 상관계수" v={v?.corrMoM == null ? DASH : num(v.corrMoM, 2)} d={corrText(v?.corrMoM)}
                   title="매달 변화율(%)끼리의 피어슨 상관. 1에 가까울수록 같은 방향·크기로 움직임" />
              <Kpi k="방향 일치율" v={v?.directionMatch == null ? DASH : `${num(v.directionMatch * 100, 0)}%`}
                   d="매달 오르내림 방향이 같았던 비율" />
              <Kpi k="비교 기간" v={v ? `${num(v.months)}개월` : DASH} d={v?.window ?? "겹치는 기간 부족 (6개월 이상 필요)"} />
            </div>
            <p className="note">기준: {data.reference.source ?? "없음"}. 두 지수는 산식·표본이 달라 수준이 아니라 움직임을 비교합니다.</p>
          </aside>
        </div>
      )}
      <section className="section">
        <div className="section-head"><h2>지역별 비교</h2><span className="sub">확정된 최근 달 기준 · 추이는 최근 24개월(잠정 포함)</span></div>
        <ErrorBox error={summary.error} onRetry={summary.reload} />
        <DataTable
          rows={regions}
          rowKey={(r) => r.regionId}
          selectedKey={rid}
          onRowClick={(r) => setParams(route, { region: r.regionId })}
          initialSort={{ key: "id", dir: "asc" }}
          columns={[
            { key: "id", header: "지역", cell: (r) => <span className="name">{sidoName(r.regionId)}</span>, sort: (r) => r.regionId },
            { key: "period", header: "기준월", cell: (r) => { const h = r.confirmed ?? r; return <>{ymLabel(h.period)} {h.provisional && <Badge tone="warn">잠정</Badge>}</>; } },
            { key: "value", header: "지수", align: "right", cell: (r) => <strong>{num((r.confirmed ?? r).value, 2)}</strong>, sort: (r) => (r.confirmed ?? r).value },
            { key: "mom", header: "전월비", align: "right", cell: (r) => <Change v={(r.confirmed ?? r).mom} digits={2} />, sort: (r) => (r.confirmed ?? r).mom },
            { key: "yoy", header: "전년비", align: "right", cell: (r) => <Change v={(r.confirmed ?? r).yoy} digits={2} />, sort: (r) => (r.confirmed ?? r).yoy },
            { key: "spark", header: "24개월", cell: (r) => <Sparkline values={r.spark} label={`${sidoName(r.regionId)} 지수 추이`} /> },
            { key: "corr", header: "R-ONE 상관", align: "right", cell: (r) => (r.corrMoM == null ? DASH : num(r.corrMoM, 2)), sort: (r) => r.corrMoM },
            { key: "dir", header: "방향 일치", align: "right", cell: (r) => (r.directionMatch == null ? DASH : `${num(r.directionMatch * 100, 0)}%`), sort: (r) => r.directionMatch },
          ]}
          foot={<span>행을 누르면 위 차트가 그 지역으로 바뀝니다. 거래가 적은 시도는 신뢰구간이 넓고 상관이 낮게 나올 수 있습니다.</span>}
        />
      </section>
    </>
  );
}
