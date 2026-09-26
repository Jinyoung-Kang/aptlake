import { useEffect, useMemo } from "react";
import { useApi, useAvailable, type MonthRow } from "../lib/api";
import { axisX, axisY, base, Chart, M2, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { MonthPicker, MonthRangePicker, RangePills } from "../components/MonthPicker";
import RegionPicker from "../components/RegionPicker";
import { Badge, Change, Empty, ErrorBox, Kpi, RangeBar, Skeleton, Sqm, StatRow, Tabs } from "../components/ui";
import { DASH, manwon, monthRange, num, ymAdd, ymLabel } from "../lib/format";
import { href, navigate, setParams, type Route } from "../lib/router";
import { recentRegions, useRegions } from "../lib/regions";

type MonthsResp = { region: { sggCd: string; name: string; fullName: string }; items: MonthRow[]; notes: string[]; disclaimer: string };
type Dist = {
  month: string; provisional: boolean;
  stats: { reported: number; cancelled: number; sample: number; p05: number | null; p25: number | null; median: number | null; p75: number | null; p95: number | null } | null;
  histogram: { lo: number; hi: number; n: number }[]; binWidth: number;
  byArea: { band: string; n: number; medianPpm2: number | null; medianPrice: number | null }[];
  byFloor: { band: string; n: number; medianPpm2: number | null }[];
  points: [number, number, number | null, number, number][]; notes: string[];
};
type ComplexRow = { complexKey: string; aptName: string; umdName: string; buildYear: number | null; trades: number; cancelled: number; medianPpm2: number | null; lastDate: string; lastPrice: number; lastArea: number };
type Tab = "overview" | "distribution" | "complexes";

const WEB_MAX_MONTHS = 60;

function PriceVolumeChart({ rows }: { rows: MonthRow[] }) {
  const build = (p: Palette) => {
    const x = rows.map((r) => r.dealYm);
    const prov = rows.filter((r) => r.provisional).map((r) => r.dealYm);
    const provArea = prov.length ? [[{ xAxis: prov[0] }, { xAxis: prov[prov.length - 1] }]] : [];
    const b = base(p);
    return {
      ...b,
      legend: { ...b.legend, data: ["m²당 중위가", "1~3사분위", "거래(해제 제외)", "해제"] },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      tooltip: {
        ...b.tooltip,
        formatter: (ps: { dataIndex: number }[]) => {
          const r = rows[ps[0].dataIndex];
          return `<b>${ymLabel(r.dealYm)}</b>${r.provisional ? " (잠정)" : ""}<br/>${M2}당 중위가 <b>${num(r.medianPricePerM2)}</b> 만원/${M2}` +
            `<br/>1~3사분위 ${num(r.p25PricePerM2)} ~ ${num(r.p75PricePerM2)}<br/>거래 ${num(r.trades)}건 · 해제 ${num(r.cancelled)}건 · 표본 ${num(r.sampleSize)}건` +
            (r.lowSample ? "<br/>표본 5건 미만 — 분위수 생략" : "");
        },
      },
      grid: [
        { left: 8, right: 12, top: 30, height: "54%", containLabel: true },
        { left: 8, right: 12, top: "70%", bottom: 6, containLabel: true },
      ],
      xAxis: [
        axisX(p, { type: "category", data: x, gridIndex: 0, axisLabel: { show: false }, boundaryGap: true }),
        axisX(p, { type: "category", data: x, gridIndex: 1, axisLabel: { color: p.text3, fontSize: 11, formatter: (v: string) => ymLabel(v) } }),
      ],
      yAxis: [
        axisY(p, { type: "value", gridIndex: 0, scale: true, name: "만원/m²", nameGap: 8, splitNumber: 4 }),
        axisY(p, { type: "value", gridIndex: 1, name: "건", nameGap: 8, splitNumber: 2 }),
      ],
      series: [
        { name: "base", type: "line", stack: "band", xAxisIndex: 0, yAxisIndex: 0, data: rows.map((r) => r.p25PricePerM2), symbol: "none", lineStyle: { opacity: 0 }, tooltip: { show: false }, silent: true },
        { name: "1~3사분위", type: "line", stack: "band", xAxisIndex: 0, yAxisIndex: 0, symbol: "none", lineStyle: { opacity: 0 },
          data: rows.map((r) => (r.p25PricePerM2 != null && r.p75PricePerM2 != null ? r.p75PricePerM2 - r.p25PricePerM2 : null)),
          areaStyle: { color: p.band }, itemStyle: { color: p.band } },
        { name: "m²당 중위가", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: rows.map((r) => r.medianPricePerM2), symbol: "circle", symbolSize: 5, showSymbol: rows.length <= 24,
          lineStyle: { width: 2, color: p.s1 }, itemStyle: { color: p.s1 }, connectNulls: false,
          markArea: { silent: true, itemStyle: { color: p.grid, opacity: 0.6 }, label: { show: true, color: p.text3, fontSize: 11, position: "insideTop" }, data: provArea.map((a) => [{ ...a[0], name: "잠정" }, a[1]]) } },
        { name: "거래(해제 제외)", type: "bar", stack: "vol", xAxisIndex: 1, yAxisIndex: 1, data: rows.map((r) => r.trades), barMaxWidth: 18, itemStyle: { color: p.s1 } },
        { name: "해제", type: "bar", stack: "vol", xAxisIndex: 1, yAxisIndex: 1, data: rows.map((r) => r.cancelled), barMaxWidth: 18, itemStyle: { color: p.s2, borderRadius: [3, 3, 0, 0] } },
      ],
    };
  };
  return <Chart build={build} deps={[rows]} height={430} label="월별 m²당 가격 중위수와 1~3사분위, 거래량 결합 차트" />;
}

function Overview({ rows, all, from }: { rows: MonthRow[]; all: MonthRow[]; from: string }) {
  const valid = rows.filter((r) => r.medianPricePerM2 != null);
  const last12 = rows.slice(-12);
  const t12 = last12.reduce((a, r) => a + r.trades, 0);
  const c12 = last12.reduce((a, r) => a + r.cancelled, 0);
  const meds = last12.map((r) => r.medianPricePerM2).filter((v): v is number => v != null);
  const lastOk = [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1];
  const tableRows = all.filter((r) => r.dealYm >= from).reverse();
  const byYm = new Map(all.map((r) => [r.dealYm, r])); // 전년비는 표시 기간 앞 12개월까지 받아 계산
  return (
    <>
      <div className="layout">
        <div className="panel panel-pad"><PriceVolumeChart rows={rows} /></div>
        <aside>
          <div className="section-head"><h2>주요 지표</h2><span className="sub">표시 기간 기준</span></div>
          <div className="stat-list one">
            <StatRow k="최근 12개월 거래" v={`${num(t12)}건`} />
            <StatRow k="최근 12개월 해제율" v={t12 + c12 ? `${num((c12 / (t12 + c12)) * 100, 1)}%` : DASH} />
            <StatRow k="최근 확정 월 중위가" v={lastOk ? `${num(lastOk.medianPricePerM2)} 만원/m²` : DASH} />
            <StatRow k="12개월 중위가 범위" v={meds.length ? `${num(Math.min(...meds))} ~ ${num(Math.max(...meds))}` : DASH} />
          </div>
          {meds.length > 1 && lastOk?.medianPricePerM2 != null && (
            <>
              <RangeBar lo={Math.min(...meds)} hi={Math.max(...meds)} v={lastOk.medianPricePerM2} />
              <div className="note">막대 = 최근 12개월 월별 중위가의 최저~최고, 세로선 = 최근 확정 월</div>
            </>
          )}
          <p className="note">잠정 = 계약월 말일 + 60일 전 (신고가 계속 추가됨). 분위수는 해제·면적 0·월 전국 상하위 0.1% 제외, 표본 5건 미만 월은 생략.</p>
        </aside>
      </div>
      <section className="section" style={{ marginTop: 22 }}>
        <div className="section-head"><h2>월별 데이터</h2><span className="sub">최신 월부터 · 단위 만원/<Sqm /></span></div>
        <DataTable
          rows={tableRows}
          rowKey={(r) => r.dealYm}
          columns={[
            { key: "ym", header: "계약월", cell: (r) => <span className="name">{ymLabel(r.dealYm)} {r.provisional && <Badge tone="warn">잠정</Badge>} {r.lowSample && <Badge>표본 부족</Badge>}</span> },
            { key: "trades", header: "거래", align: "right", cell: (r) => num(r.trades), sort: (r) => r.trades },
            { key: "cancel", header: "해제", align: "right", cell: (r) => num(r.cancelled), sort: (r) => r.cancelled },
            { key: "rate", header: "해제율", align: "right", cell: (r) => (r.reported ? `${num((r.cancelled / r.reported) * 100, 1)}%` : DASH), sort: (r) => (r.reported ? r.cancelled / r.reported : null) },
            { key: "p25", header: "1사분위", align: "right", cell: (r) => num(r.p25PricePerM2), sort: (r) => r.p25PricePerM2 },
            { key: "med", header: "중위가", align: "right", cell: (r) => <strong>{num(r.medianPricePerM2)}</strong>, sort: (r) => r.medianPricePerM2 },
            { key: "p75", header: "3사분위", align: "right", cell: (r) => num(r.p75PricePerM2), sort: (r) => r.p75PricePerM2 },
            { key: "yoy", header: "중위가 전년비", align: "right", cell: (r) => {
              const prev = byYm.get(ymAdd(r.dealYm, -12));
              return <Change v={prev?.medianPricePerM2 && r.medianPricePerM2 ? (r.medianPricePerM2 / prev.medianPricePerM2 - 1) * 100 : null} />;
            } },
          ]}
          maxHeight={460}
        />
      </section>
    </>
  );
}

function Distribution({ sgg, ym, min, max, onMonth }: { sgg: string; ym: string; min: string; max: string; onMonth: (v: string) => void }) {
  const { data, error, loading, stale, reload } = useApi<Dist>(`/v1/regions/${sgg}/distribution?ym=${ym}`);
  const s = data?.stats;
  const hist = (p: Palette) => {
    const b = base(p);
    const h = data?.histogram ?? [];
    const lines = s ? [{ name: "중위", xAxis: s.median }, { name: "1사분위", xAxis: s.p25 }, { name: "3사분위", xAxis: s.p75 }] : [];
    return {
      ...b,
      legend: { show: false },
      tooltip: { ...b.tooltip, trigger: "item", formatter: (x: { data: [number, number, number] }) => `${num(x.data[0])} ~ ${num(x.data[2])} 만원/${M2}<br/><b>${num(x.data[1])}건</b>` },
      xAxis: axisX(p, { type: "value", scale: true, name: "만원/m²", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: p.text3 }, splitLine: { show: false } }),
      yAxis: axisY(p, { type: "value", name: "건", minInterval: 1 }),
      series: [{
        type: "bar", barWidth: "96%",
        data: h.map((x) => [(x.lo + x.hi) / 2, x.n, x.hi]),
        itemStyle: { color: p.s1 },
        markLine: { symbol: "none", silent: true, label: { formatter: "{b}", color: p.text2, fontSize: 11 },
                    lineStyle: { color: p.text, type: "dashed", width: 1 }, data: lines },
      }],
    };
  };
  const scatter = (p: Palette) => {
    const b = base(p);
    const pts = data?.points ?? [];
    const normal = pts.filter((x) => !x[3] && !x[4]).map((x) => [x[0], x[1], x[2]]);
    const cancelled = pts.filter((x) => x[3]).map((x) => [x[0], x[1], x[2]]);
    const outlier = pts.filter((x) => !x[3] && x[4]).map((x) => [x[0], x[1], x[2]]);
    return {
      ...b,
      legend: { ...b.legend, data: ["정상 거래", "해제", "이상치"] },
      tooltip: { ...b.tooltip, trigger: "item", formatter: (x: { seriesName: string; data: [number, number, number | null] }) => `${x.seriesName}<br/>전용 ${num(x.data[0], 2)}${M2} · ${x.data[2] ?? DASH}층<br/><b>${manwon(x.data[1])}원</b>` },
      xAxis: axisX(p, { type: "value", scale: true, name: "전용면적(m²)", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: p.text3 }, splitLine: { lineStyle: { color: p.grid } } }),
      yAxis: axisY(p, { type: "value", scale: true, name: "거래금액(억)", axisLabel: { color: p.text3, formatter: (v: number) => num(v / 10000, 0) } }),
      series: [
        { name: "정상 거래", type: "scatter", data: normal, symbolSize: 6, itemStyle: { color: p.s1, opacity: 0.55 } },
        { name: "해제", type: "scatter", data: cancelled, symbolSize: 8, itemStyle: { color: "transparent", borderColor: p.s2, borderWidth: 1.5 } },
        { name: "이상치", type: "scatter", data: outlier, symbolSize: 7, symbol: "diamond", itemStyle: { color: p.text3 } },
      ],
    };
  };
  return (
    <>
      <div className="toolbar">
        <MonthPicker value={ym} min={min} max={max} onChange={onMonth} label="계약월" />
        {data?.provisional && !stale && <Badge tone="warn">잠정</Badge>}
      </div>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={300} /> : null}
      <div data-stale={stale} aria-busy={loading}>
      {data && !s && !stale && <Empty>{ymLabel(ym)}에는 거래가 없습니다.</Empty>}
      {data && s && (
        <>
          <div className="kpis">
            <Kpi k="표본 (해제·이상치 제외)" v={num(s.sample)} unit="건" d={`신고 ${num(s.reported)}건 · 해제 ${num(s.cancelled)}건`} />
            <Kpi k="m²당 중위가" v={num(s.median)} unit="만원/m²" d={`1~3사분위 ${num(s.p25)} ~ ${num(s.p75)}`} />
            <Kpi k="5~95 백분위" v={`${num(s.p05)} ~ ${num(s.p95)}`} d="양 끝 5%를 뺀 가격 폭" />
          </div>
          <div className="grid-2">
            <section className="section">
              <div className="section-head"><h2><Sqm />당 가격 분포</h2><span className="sub">구간 너비 {num(data.binWidth)} 만원/<Sqm /></span></div>
              <div className="panel panel-pad"><Chart build={hist} deps={[data]} height={300} label="m²당 가격 히스토그램" /></div>
            </section>
            <section className="section">
              <div className="section-head"><h2>면적 × 거래금액</h2><span className="sub">점 하나 = 거래 하나</span></div>
              <div className="panel panel-pad"><Chart build={scatter} deps={[data]} height={300} label="전용면적과 거래금액 산점도" /></div>
            </section>
          </div>
          <div className="grid-2">
            <DataTable rows={data.byArea} rowKey={(r) => r.band} caption="면적대별" columns={[
              { key: "band", header: "전용면적(m²)", cell: (r) => <span className="name">{r.band}</span> },
              { key: "n", header: "거래", align: "right", cell: (r) => num(r.n) },
              { key: "m", header: "m²당 중위가", align: "right", cell: (r) => num(r.medianPpm2) },
              { key: "p", header: "중위 거래가", align: "right", cell: (r) => manwon(r.medianPrice) },
            ]} />
            <DataTable rows={data.byFloor} rowKey={(r) => r.band} caption="층별" columns={[
              { key: "band", header: "층", cell: (r) => <span className="name">{r.band}</span> },
              { key: "n", header: "거래", align: "right", cell: (r) => num(r.n) },
              { key: "m", header: "m²당 중위가", align: "right", cell: (r) => num(r.medianPpm2) },
            ]} />
          </div>
          <div className="note">{data.notes.map((n) => <p key={n}>{n}</p>)}</div>
        </>
      )}
      </div>
    </>
  );
}

function Complexes({ sgg, from, to }: { sgg: string; from: string; to: string }) {
  const { data, error, loading, stale, reload } = useApi<{ items: ComplexRow[] }>(`/v1/regions/${sgg}/complexes?from=${from}&to=${to}&limit=50`);
  return (
    <div data-stale={stale} aria-busy={loading}>
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={300} /> : null}
      {data && (
        <DataTable rows={data.items} rowKey={(r) => r.complexKey} onRowClick={(r) => navigate(`/complex/${r.complexKey}`)}
          initialSort={{ key: "trades", dir: "desc" }}
          columns={[
            { key: "rank", header: "#", cell: (_r, i) => <span className="rank">{i + 1}</span>, width: 36 },
            { key: "apt", header: "단지", cell: (r) => <span className="name">{r.aptName}<span className="sub">{r.umdName}{r.buildYear ? ` · ${r.buildYear}년 준공` : ""}</span></span>, sort: (r) => r.aptName },
            { key: "trades", header: "거래", align: "right", cell: (r) => num(r.trades), sort: (r) => r.trades },
            { key: "cancel", header: "해제", align: "right", cell: (r) => num(r.cancelled), sort: (r) => r.cancelled },
            { key: "med", header: "m²당 중위가", align: "right", cell: (r) => num(r.medianPpm2), sort: (r) => r.medianPpm2 },
            { key: "last", header: "최근 거래", align: "right", cell: (r) => <span>{manwon(r.lastPrice)}<span className="sub">{r.lastDate.replaceAll("-", ".")} · {num(r.lastArea, 1)}<Sqm /></span></span>, sort: (r) => r.lastDate },
          ]}
          foot={<span>{ymLabel(from)} ~ {ymLabel(to)} 거래 기준 상위 50개 단지 · 행을 누르면 단지 상세</span>}
        />
      )}
    </div>
  );
}

export default function RegionPage({ route }: { route: Route }) {
  const avail = useAvailable();
  const { byCode } = useRegions();
  const sgg = route.parts[1] ?? "";
  useEffect(() => {
    if (!/^\d{5}$/.test(sgg)) navigate(`/region/${recentRegions()[0] ?? "41135"}`, undefined, true);
  }, [sgg]);
  const tab = (route.params.get("tab") as Tab) || "overview";
  const max = avail?.to ?? "";
  const min = avail?.from ?? "";
  const to = route.params.get("to") ?? max;
  const from = route.params.get("from") ?? (max ? (ymAdd(max, -23) < min ? min : ymAdd(max, -23)) : "");
  const ym = route.params.get("ym") ?? avail?.default ?? "";
  const ok = /^\d{5}$/.test(sgg) && !!from && !!to;
  // YoY 비교를 위해 표시 기간보다 12개월 앞까지 받는다 (표·지표 계산용)
  const fetchFrom = from && min ? (ymAdd(from, -12) < min ? min : ymAdd(from, -12)) : from;
  const { data, error, loading, stale, reload } = useApi<MonthsResp>(ok ? `/v1/regions/${sgg}/months?from=${fetchFrom}&to=${to}` : null);
  const region = byCode.get(sgg);

  const rows = useMemo(() => {
    const map = new Map((data?.items ?? []).map((r) => [r.dealYm, r]));
    return monthRange(from, to).map((m) => map.get(m) ?? ({ dealYm: m, reported: 0, trades: 0, cancelled: 0, sampleSize: 0, outliers: 0, p25PricePerM2: null, medianPricePerM2: null, p75PricePerM2: null } as MonthRow));
  }, [data, from, to]);
  const full = data?.items ?? [];
  const quote = useMemo(() => {
    const valid = full.filter((r) => r.medianPricePerM2 != null && !r.lowSample);
    const cur = [...valid].reverse().find((r) => !r.provisional) ?? valid[valid.length - 1];
    if (!cur) return null;
    const prev = full.find((r) => r.dealYm === ymAdd(cur.dealYm, -12));
    return { cur, yoy: prev?.medianPricePerM2 ? (cur.medianPricePerM2! / prev.medianPricePerM2 - 1) * 100 : null };
  }, [full]);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb"><a href={href("/market")}>시장 개요</a> › {region?.sidoName ?? "지역 분석"}</div>
          <h1>{region?.name ?? data?.region.name ?? "지역 분석"}</h1>
        </div>
        <div className="tools">
          <RegionPicker value={sgg} align="right" onChange={(c) => navigate(`/region/${c}`, Object.fromEntries(route.params))} />
        </div>
      </div>
      {quote && (
        <div style={{ marginBottom: 14 }} data-stale={stale}>
          <div className="quote">
            <span className="price">{num(quote.cur.medianPricePerM2)}</span><span className="unit">만원/<Sqm /></span>
            <span className="chg"><Change v={quote.yoy} title="전년 같은 달 대비" /></span>
            <span className="muted small">전년 같은 달 대비</span>
          </div>
          <div className="quote-sub">
            <Sqm />당 거래가 중위수 · {ymLabel(quote.cur.dealYm)} 계약분 {quote.cur.provisional ? "(잠정)" : ""} · 표본 {num(quote.cur.sampleSize)}건 · 거래 {num(quote.cur.trades)}건
          </div>
        </div>
      )}
      <Tabs value={tab} onChange={(v) => setParams(route, { tab: v })} options={[
        { value: "overview", label: "개요" }, { value: "distribution", label: "가격 분포" }, { value: "complexes", label: "단지" },
      ]} />
      {tab !== "distribution" && avail && (
        <div className="toolbar">
          <RangePills from={from} to={to} min={min} max={max} maxSpan={WEB_MAX_MONTHS} onChange={(a, b) => setParams(route, { from: a, to: b })} />
          <MonthRangePicker from={from} to={to} min={min} max={max} maxSpan={WEB_MAX_MONTHS} onChange={(a, b) => setParams(route, { from: a, to: b })} />
          <span className="muted small">{monthRange(from, to).length}개월</span>
        </div>
      )}
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={420} /> : null}
      {data && tab === "overview" && <div data-stale={stale} aria-busy={loading}><Overview rows={rows} all={full} from={from} /></div>}
      {tab === "distribution" && ok && avail && (
        <Distribution sgg={sgg} ym={ym} min={min} max={max} onMonth={(v) => setParams(route, { ym: v })} />
      )}
      {tab === "complexes" && ok && <Complexes sgg={sgg} from={from} to={to} />}
      {data && tab === "overview" && <p className="note">{data.disclaimer} 변화율: 상승 <span className="chg up">▲</span> · 하락 <span className="chg down">▼</span></p>}
    </>
  );
}
