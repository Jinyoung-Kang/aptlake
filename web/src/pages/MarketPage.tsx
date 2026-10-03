import { useMemo, useRef, useState } from "react";
import type { GeoSgg, IndexSummaryItem, Overview, SggSummary } from "../api/types";
import { useApi } from "../hooks/useApi";
import { Chart, echarts, M2, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { MonthPicker } from "../components/MonthPicker";
import { Badge, Change, ErrorBox, Kpi, Segmented, Skeleton, Sparkline, Sqm, sqm, StatRow, Tabs } from "../components/ui";
import { DASH, esc, num, ymLabel } from "../lib/format";
import { mapPieces, METRICS, metricOf, metricValue, type Metric } from "../domain/market";
import { representative } from "../domain/priceIndex";
import { href, navigate, setParams, type Route } from "../lib/router";
import { useRegions } from "../lib/regions";
import { paths } from "../api/endpoints";

let registered = "";  // 등록한 경계(출처+개수) — 같은 경계를 다시 등록하지 않게

const MAP_HOME = { zoom: 1.12, center: [127.8, 36.1] as [number, number] };
const MAP_ZOOM = { min: 0.9, max: 12 };

function MapPanel({ data, metric, geo }: { data: Overview; metric: Metric; geo: GeoSgg }) {
  const { byCode } = useRegions();
  // 지도 시점은 지표를 바꿔도 유지 (드래그 이동은 차트가, 확대는 버튼이 바꾼다)
  const view = useRef({ ...MAP_HOME });
  const chart = useRef<echarts.ECharts | null>(null);
  const setView = (zoom: number, center?: [number, number]) => {
    view.current = { zoom: Math.min(MAP_ZOOM.max, Math.max(MAP_ZOOM.min, zoom)), center: center ?? view.current.center };
    chart.current?.setOption({ series: [{ zoom: view.current.zoom, center: view.current.center }] });
  };
  if (registered !== geo.source + geo.features.length) {
    echarts.registerMap("kr-sgg", {
      type: "FeatureCollection",
      features: geo.features.map((f) => ({ ...f, properties: { ...f.properties, name: f.properties.sggCd } })),
    } as never);
    registered = geo.source + geo.features.length;
  }
  const values = useMemo(() => {
    const m = new Map<string, SggSummary>();
    data.sgg.forEach((s) => m.set(s.sggCd, s));
    return m;
  }, [data]);
  const meta = METRICS.find((m) => m.value === metric)!;

  const build = (p: Palette) => {
    const series = geo.features.map((f) => {
      const code = f.properties.sggCd;
      const v = metricValue(values.get(code), metric);
      return { name: code, value: v ?? "-" };
    });
    const pieces = mapPieces(series.map((x) => x.value).filter((v): v is number => typeof v === "number"), metric, p);
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item", confine: true, backgroundColor: p.surface, borderColor: p.border, textStyle: { color: p.text, fontSize: 12 },
        formatter: (x: { name: string }) => {
          const r = byCode.get(x.name);
          const s = values.get(x.name);
          const title = r ? `<b>${esc(r.name)}</b> <span class="tt-muted">${esc(r.sidoName)}</span>` : esc(x.name);
          if (!s) return `${title}<br/>이 달 거래 없음`;
          const yoy = s.medianYoY == null ? DASH : `${s.medianYoY > 0 ? "▲ +" : s.medianYoY < 0 ? "▼ " : ""}${s.medianYoY.toFixed(1)}%`;
          return `${title}<br/>${M2}당 중위가 <b>${s.lowSample || s.median == null ? DASH : num(s.median)}</b> 만원/${M2}` +
            `<br/>전년비 ${yoy} · 거래 ${num(s.trades)}건 · 해제율 ${s.cancelRate == null ? DASH : `${s.cancelRate.toFixed(1)}%`}` +
            `<br/><span class="tt-muted">표본 ${num(s.sample)}건 — 클릭하면 지역 분석</span>`;
        },
      },
      visualMap: {
        type: "piecewise", pieces, left: 8, bottom: 8, orient: "vertical", itemWidth: 14, itemHeight: 10, itemGap: 4,
        textStyle: { color: p.text2, fontSize: 11 }, outOfRange: { color: p.na },
        text: [meta.unit, ""], showLabel: true,
      },
      series: [{
        // 휠 확대는 끈다 — 페이지를 스크롤하다 지도 위에서 휠이 확대로 바뀌는 문제 (확대는 버튼으로)
        type: "map", map: "kr-sgg", roam: "move", aspectScale: 0.82, zoom: view.current.zoom, center: view.current.center, scaleLimit: MAP_ZOOM,
        selectedMode: false, data: series, nameProperty: "name",
        itemStyle: { areaColor: p.na, borderColor: p.surface, borderWidth: 0.5 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: undefined, borderColor: p.text, borderWidth: 1.2 } },
        label: { show: false },
      }],
    };
  };
  return (
    <>
      <div className="map-tools" role="group" aria-label="지도 확대">
        <button type="button" className="btn icon" aria-label="확대" title="확대" onClick={() => setView(view.current.zoom * 1.5)}>+</button>
        <button type="button" className="btn icon" aria-label="축소" title="축소" onClick={() => setView(view.current.zoom / 1.5)}>−</button>
        <button type="button" className="btn" title="전국 보기로" onClick={() => setView(MAP_HOME.zoom, MAP_HOME.center)}>초기화</button>
      </div>
      <Chart build={build} deps={[data, metric, geo]} height={600} label={`시군구별 ${meta.label} 지도`}
             onReady={(c) => {
               chart.current = c;
               c.on("georoam", () => {
                 const s = (c.getOption() as { series?: { zoom?: number; center?: [number, number] }[] }).series?.[0];
                 if (s?.center) view.current = { zoom: s.zoom ?? view.current.zoom, center: s.center };
               });
             }}
             onClick={(e) => { if (e.name && /^\d{5}$/.test(e.name)) navigate(`/region/${e.name}`); }} />
    </>
  );
}

function RankList({ rows, kind }: { rows: SggSummary[]; kind: "volume" | "gainers" | "losers" }) {
  const { byCode } = useRegions();
  if (!rows.length) return <div className="empty">표본 조건을 만족하는 시군구가 없습니다.</div>;
  return (
    <div className="rank-list">
      {rows.map((s, i) => {
        const r = byCode.get(s.sggCd);
        return (
          <a key={s.sggCd} className="row" href={href(`/region/${s.sggCd}`)} style={{ color: "inherit", textDecoration: "none" }}>
            <span className="i">{i + 1}</span>
            <span className="nm">{r?.name ?? s.sggCd}<small>{r?.sidoName}</small></span>
            <span className="num">{kind === "volume" ? `${num(s.trades)}건` : `${num(s.median)}`}</span>
            <span className="num">{kind === "volume" ? <Change v={s.tradesMoM} title="거래 전월 대비" /> : <Change v={s.medianYoY} title="중위가 전년 대비" />}</span>
          </a>
        );
      })}
    </div>
  );
}

export default function MarketPage({ route }: { route: Route }) {
  const ym = route.params.get("ym");
  const metric = metricOf(route.params.get("metric"));
  const { data, error, loading, stale, reload } = useApi<Overview>(paths.marketOverview(ym));
  const geo = useApi<GeoSgg>(paths.geoSgg());
  const idx = useApi<{ items: IndexSummaryItem[] }>(paths.indexSummary());
  const { sidoName } = useRegions();
  const [rankTab, setRankTab] = useState<"volume" | "gainers" | "losers">("volume");
  const nation = data?.nation;
  const natAll = idx.data?.items.find((x) => x.regionId === "00");
  const natIdx = natAll ? representative(natAll) : undefined;  // 대표값 = 확정된 최근 달

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">시장 개요</div>
          <h1>전국 아파트 매매 시장</h1>
        </div>
        <div className="tools">
          {data && (
            <MonthPicker value={ym ?? data.month} min={data.available.from} max={data.available.to}
                         onChange={(v) => setParams(route, { ym: v })} />
          )}
          {data?.provisional && !stale && <Badge tone="warn" title="계약월 말일 + 60일 전까지는 신고가 계속 추가됩니다">잠정</Badge>}
        </div>
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={90} /> : null}
      <div data-stale={stale} aria-busy={loading}>
      {data && (
        <div className="kpis">
          <Kpi k={`전국 거래 · ${ymLabel(data.month)}`} v={num(nation?.trades)} unit="건"
               d={<>전월 대비 <Change v={nation?.tradesMoM} /> · 전년 대비 <Change v={nation?.tradesYoY} /></>} />
          <Kpi k="전국 m²당 중위가" v={num(nation?.median)} unit="만원/m²"
               d={<>전년 같은 달 대비 <Change v={nation?.medianYoY} /> · 표본 {num(nation?.sample)}건</>} />
          <Kpi k="해제율" v={nation?.cancelRate == null ? DASH : num(nation.cancelRate, 1)} unit="%"
               d={`해제 ${num(nation?.cancelled)}건 (신고 후 계약 해제)`} />
          <Kpi k={`자체 지수(전국) · ${ymLabel(natIdx?.period ?? null)}`} v={natIdx ? num(natIdx.value, 1) : DASH}
               d={natIdx ? <>전월 대비 <Change v={natIdx.mom} digits={2} /> · 전년 대비 <Change v={natIdx.yoy} digits={2} /></> : "산출 전"} />
        </div>
      )}
      {data && (
        <div className="layout wide-aside">
          <div>
            <section className="section">
              <div className="section-head">
                <h2>시군구 지도</h2>
                <span className="sub">{ymLabel(data.month)} · 색이 진할수록 큼 · 회색 = 자료 없음/표본 부족 · 끌어서 이동, 오른쪽 위 버튼으로 확대</span>
                <div className="tools">
                  <Segmented label="지도 지표" value={metric} onChange={(v) => setParams(route, { metric: v })}
                             options={METRICS.map((m) => ({ value: m.value, label: m.label, title: m.title }))} />
                </div>
              </div>
              <div className="panel map-wrap">
                {geo.data ? <MapPanel data={data} metric={metric} geo={geo.data} /> : geo.error ? <ErrorBox error={geo.error} onRetry={geo.reload} /> : <Skeleton h={600} />}
              </div>
              <p className="note">
                {sqm(METRICS.find((m) => m.value === metric)?.title)}. 구간은 이 달 시군구 값의 분위(6등분)로 나눕니다
                {metric === "medianYoY" ? "(변화율은 고정 구간)" : ""}. 경계: {geo.data?.source ?? "국토정보플랫폼 V-World"}.
              </p>
            </section>
          </div>
          <aside>
            <section className="section">
              <div className="section-head"><h2>시군구 순위</h2><span className="sub">{ymLabel(data.month)}</span></div>
              <Tabs value={rankTab} onChange={setRankTab} options={[
                { value: "volume", label: "거래량" }, { value: "gainers", label: "상승률" }, { value: "losers", label: "하락률" },
              ]} />
              <div className="panel">
                <RankList rows={data.rankings[rankTab]} kind={rankTab} />
              </div>
              <p className="note">
                {rankTab === "volume" ? "거래량 순 · 변화는 전월 대비 거래 건수" : <><Sqm />당 중위가 전년 동월 대비 · 두 달 모두 표본 30건 이상인 시군구만 (작은 표본의 착시 방지)</>}
              </p>
            </section>
            <section className="section">
              <div className="section-head"><h2>전국 요약</h2></div>
              <div className="stat-list one">
                <StatRow k="신고 건수" v={`${num((nation?.trades ?? 0) + (nation?.cancelled ?? 0))}건`} />
                <StatRow k="m²당 가격 1사분위 ~ 3사분위" v={`${num(nation?.p25)} ~ ${num(nation?.p75)}`} />
                <StatRow k="자료 기준" v={`${data.datasetVersion}`} />
              </div>
            </section>
          </aside>
        </div>
      )}
      {data && (
        <section className="section">
          <div className="section-head"><h2>시도별 현황</h2><span className="sub">{ymLabel(data.month)} · 추이는 최근 12개월 거래량</span></div>
          <DataTable
            rows={data.sido}
            rowKey={(r) => r.regionId}
            onRowClick={(r) => navigate("/index", { region: r.regionId })}
            initialSort={{ key: "trades", dir: "desc" }}
            columns={[
              { key: "name", header: "시도", cell: (r) => <span className="name">{sidoName(r.regionId)}</span>, sort: (r) => r.regionId },
              { key: "trades", header: "거래(건)", align: "right", cell: (r) => num(r.trades), sort: (r) => r.trades },
              { key: "mom", header: "전월비", align: "right", cell: (r) => <Change v={r.tradesMoM} />, sort: (r) => r.tradesMoM },
              { key: "yoy", header: "전년비", align: "right", cell: (r) => <Change v={r.tradesYoY} />, sort: (r) => r.tradesYoY },
              { key: "median", header: "m²당 중위가", align: "right", cell: (r) => num(r.median), sort: (r) => r.median },
              { key: "myoy", header: "중위가 전년비", align: "right", cell: (r) => <Change v={r.medianYoY} />, sort: (r) => r.medianYoY },
              { key: "cancel", header: "해제율", align: "right", cell: (r) => (r.cancelRate == null ? DASH : `${num(r.cancelRate, 1)}%`), sort: (r) => r.cancelRate },
              { key: "spark", header: "12개월 거래", cell: (r) => <Sparkline values={r.spark.trades} label={`${sidoName(r.regionId)} 최근 12개월 거래량`} /> },
            ]}
            foot={<><span>행을 누르면 그 시도의 가격지수로 이동합니다.</span><span>{data.disclaimer}</span></>}
          />
        </section>
      )}
      </div>
    </>
  );
}
