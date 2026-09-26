import { useMemo, useState } from "react";
import { useApi, type Trade } from "../lib/api";
import { axisX, axisY, base, Chart, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { Badge, ErrorBox, Kpi, Skeleton } from "../components/ui";
import { DASH, manwon, num, quantile } from "../lib/format";
import { href, type Route } from "../lib/router";
import { useRegions } from "../lib/regions";

type Resp = {
  complex: { complexKey: string; sggCd: string; umdName: string; jibun: string | null; aptName: string; buildYear: number | null; landLeasehold: boolean; firstSeen: string; validTrades: number };
  recentTrades: Trade[];
  history: [string, number, number | null, number, number, number, number][];
};
const BANDS = [
  { key: "all", label: "전체", lo: 0, hi: Infinity }, { key: "s", label: "60㎡ 미만", lo: 0, hi: 60 },
  { key: "m", label: "60~85㎡", lo: 60, hi: 85 }, { key: "l", label: "85~135㎡", lo: 85, hi: 135 }, { key: "xl", label: "135㎡ 이상", lo: 135, hi: Infinity },
];

export default function ComplexPage({ route }: { route: Route }) {
  const key = route.parts[1] ?? "";
  const { byCode } = useRegions();
  const { data, error, loading, stale } = useApi<Resp>(/^c_[0-9a-f]{20}$/.test(key) ? `/v1/complexes/${key}` : null);
  const [band, setBand] = useState("all");
  const b = BANDS.find((x) => x.key === band)!;
  const hist = useMemo(() => (data?.history ?? []).filter((h) => h[1] >= b.lo && h[1] < b.hi), [data, b]);
  const present = useMemo(() => BANDS.filter((x) => x.key === "all" || (data?.history ?? []).some((h) => h[1] >= x.lo && h[1] < x.hi)), [data]);
  const valid = hist.filter((h) => !h[5] && !h[6]);
  const med = quantile(valid.map((h) => h[4]), 0.5);

  const build = (p: Palette) => {
    const bs = base(p);
    return {
      ...bs,
      legend: { ...bs.legend, data: ["거래", "해제"] },
      tooltip: { ...bs.tooltip, trigger: "item", formatter: (x: { seriesName: string; data: [string, number, number, number | null, number] }) =>
        `${x.seriesName} · ${x.data[0].replaceAll("-", ".")}<br/>㎡당 <b>${num(x.data[1])}</b>만원 · ${manwon(x.data[4])}원<br/>전용 ${num(x.data[2], 2)}㎡ · ${x.data[3] ?? DASH}층` },
      xAxis: axisX(p, { type: "time", splitLine: { show: false } }),
      yAxis: axisY(p, { type: "value", scale: true, name: "만원/㎡" }),
      series: [
        { name: "거래", type: "scatter", symbolSize: 7, itemStyle: { color: p.s1, opacity: 0.7 },
          data: hist.filter((h) => !h[5]).map((h) => [h[0], h[4], h[1], h[2], h[3]]) },
        { name: "해제", type: "scatter", symbolSize: 8, itemStyle: { color: "transparent", borderColor: p.s2, borderWidth: 1.5 },
          data: hist.filter((h) => h[5]).map((h) => [h[0], h[4], h[1], h[2], h[3]]) },
      ],
    };
  };

  const c = data?.complex;
  const region = c ? byCode.get(c.sggCd) : undefined;
  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">
            단지 · {region ? <a href={href(`/region/${region.sggCd}`)}>{region.fullName}</a> : ""} {c?.umdName ?? ""} {c?.jibun ?? ""}
          </div>
          <h1>{c?.aptName ?? "단지"}</h1>
        </div>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Skeleton h={300} /> : null}
      {c && (
        <div data-stale={stale} aria-busy={loading}>
          <div className="kpis">
            <Kpi k="유효 거래 (전 기간)" v={num(c.validTrades)} unit="건" d={`처음 관측 ${c.firstSeen.replaceAll("-", ".")}`} />
            <Kpi k="준공" v={c.buildYear ? `${c.buildYear}년` : DASH} d={c.landLeasehold ? "토지임대부" : "일반"} />
            <Kpi k={`㎡당 중위가 · ${b.label}`} v={num(med)} unit="만원/㎡" d={`표시 거래 ${num(valid.length)}건 기준`} />
          </div>
          <section className="section">
            <div className="section-head">
              <h2>거래 이력</h2><span className="sub">점 하나 = 거래 하나 · 속 빈 원 = 해제</span>
              <div className="tools">
                <div className="chips" role="group" aria-label="전용면적">
                  {present.map((x) => <button key={x.key} type="button" className="chip" aria-pressed={x.key === band} onClick={() => setBand(x.key)}>{x.label}</button>)}
                </div>
              </div>
            </div>
            <div className="panel panel-pad"><Chart build={build} deps={[hist]} height={340} label="단지 거래 이력 산점도" /></div>
          </section>
          <section className="section">
            <div className="section-head"><h2>최근 거래</h2><span className="sub">최근 20건</span></div>
            <DataTable rows={data!.recentTrades} rowKey={(t) => t.tradeId} columns={[
              { key: "d", header: "계약일", cell: (t) => t.dealDate.replaceAll("-", ".") },
              { key: "a", header: "전용㎡", align: "right", cell: (t) => num(t.areaM2, 2) },
              { key: "f", header: "층", align: "right", cell: (t) => t.floor ?? DASH },
              { key: "p", header: "거래금액", align: "right", cell: (t) => <strong>{manwon(t.priceManwon)}</strong> },
              { key: "u", header: "㎡당(만원)", align: "right", cell: (t) => num(t.pricePerM2) },
              { key: "s", header: "상태", cell: (t) => <>{t.cancelled && <Badge tone="bad">해제</Badge>} {t.version > 1 && <Badge tone="info">v{t.version}</Badge>} {t.registeredDate && !t.cancelled && <Badge tone="good">등기</Badge>}</> },
            ]} />
          </section>
        </div>
      )}
    </>
  );
}
