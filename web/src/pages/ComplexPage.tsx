import { useMemo, useState } from "react";
import type { ComplexDetail } from "../api/types";
import { useApi } from "../hooks/useApi";
import { axisX, axisY, base, Chart, M2, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { Badge, Empty, ErrorBox, Group, Kpi, Skeleton, sqm } from "../components/ui";
import { DASH, manwon, num, ymLabel } from "../lib/format";
import { bandOf, hasTrades, inBand, presentBands, validMedian } from "../domain/complex";
import { COUNT_INCOMPLETE_NOTE, PUBLISHED_BASIS, RATIO_INCOMPLETE_NOTE } from "../domain/rent";
import { href, type Route } from "../lib/router";
import { useRegions } from "../lib/regions";
import { paths } from "../api/endpoints";

export default function ComplexPage({ route }: { route: Route }) {
  const key = route.parts[1] ?? "";
  const { byCode } = useRegions();
  const { data, error, loading, stale, reload } = useApi<ComplexDetail>(/^c_[0-9a-f]{20}$/.test(key) ? paths.complex(key) : null);
  const [band, setBand] = useState("all");
  const b = bandOf(band);
  const hist = useMemo(() => inBand(data?.history ?? [], b), [data, b]);
  const present = useMemo(() => presentBands(data?.history ?? []), [data]);
  const { median: med, n: validN } = validMedian(hist);

  const build = (p: Palette) => {
    const bs = base(p);
    return {
      ...bs,
      legend: { ...bs.legend, data: ["거래", "해제"] },
      tooltip: { ...bs.tooltip, trigger: "item", formatter: (x: { seriesName: string; data: [string, number, number, number | null, number] }) =>
        `${x.seriesName} · ${x.data[0].replaceAll("-", ".")}<br/>${M2}당 <b>${num(x.data[1])}</b>만원 · ${manwon(x.data[4])}원<br/>전용 ${num(x.data[2], 2)}${M2} · ${x.data[3] ?? DASH}층` },
      xAxis: axisX(p, { type: "time", splitLine: { show: false } }),
      yAxis: axisY(p, { type: "value", scale: true, name: "만원/m²" }),
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
  // 전월세에만 있는 단지(ADR-050): 매매 구역 대신 사실 문구
  const traded = data ? hasTrades(data) : true;
  // 단지 창(12개월)이 완결이 아니면 계약 수·중앙값은 발행된 계약만 센 값 — 머리글에 기준을 붙이고, 구간이 없어도 '계약 없음'으로 단정하지 않는다
  // complete 가 없는 응답(필드를 넣기 전 API 본문이 결과 캐시·ETag 304 로 남은 때)은 미완결로 보지 않는다 — 지역 전세가율(complete === false)과 같은 규칙
  const windowOff = data?.rent?.window.complete === false;
  // 그때 구간별 비율도 모두 null — 표본 부족과 구분해 까닭을 적는다 (구간이 없으면 빈 표 문구가 까닭을 적는다)
  const ratioOff = windowOff && (data?.rent?.byArea.length ?? 0) > 0;
  const rentEmpty = windowOff && data?.rent
    ? `발행된 전월세 계약이 없습니다 — ${ymLabel(data.rent.window.from)} ~ ${ymLabel(data.rent.window.to)} ${COUNT_INCOMPLETE_NOTE}.`
    : "이 기간 전월세 계약이 없습니다.";
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
      <ErrorBox error={error} onRetry={reload} />
      {loading && !data ? <Skeleton h={300} /> : null}
      {c && (
        <div data-stale={stale} aria-busy={loading}>
          <div className="kpis">
            <Kpi k="유효 거래 (전 기간)" v={num(c.validTrades)} unit="건" d={c.firstSeen ? `처음 관측 ${c.firstSeen.replaceAll("-", ".")}` : undefined} />
            {/* 토지임대부는 매매 원천에만 있는 항목 — 전월세에만 나온 단지는 API 가 null(미상)로 준다 */}
            <Kpi k="준공" v={c.buildYear ? `${c.buildYear}년` : DASH} d={c.landLeasehold == null ? "토지임대부 여부 미상 (매매 원천에만 있는 항목)" : c.landLeasehold ? "토지임대부" : "일반"} />
            <Kpi k={`m²당 중위가 · ${b.label}`} v={num(med)} unit="만원/m²" d={traded ? `표시 거래 ${num(validN)}건 기준` : "매매 거래 없음"} />
          </div>
          {traded ? (
            <>
              <section className="section">
                <div className="section-head">
                  <h2>거래 이력</h2><span className="sub">점 하나 = 거래 하나 · 속 빈 원 = 해제</span>
                  <div className="tools">
                    <Group label="전용면적" className="chips">
                      {present.map((x) => <button key={x.key} type="button" className="chip" aria-pressed={x.key === band} onClick={() => setBand(x.key)}>{sqm(x.label)}</button>)}
                    </Group>
                  </div>
                </div>
                <div className="panel panel-pad"><Chart build={build} deps={[hist]} height={340} label="단지 거래 이력 산점도" /></div>
              </section>
              <section className="section">
                <div className="section-head"><h2>최근 거래</h2><span className="sub">최근 20건</span></div>
                <DataTable rows={data?.recentTrades ?? []} rowKey={(t) => t.tradeId} columns={[
                  { key: "d", header: "계약일", cell: (t) => t.dealDate.replaceAll("-", ".") },
                  { key: "a", header: "전용m²", align: "right", cell: (t) => num(t.areaM2, 2) },
                  { key: "f", header: "층", align: "right", cell: (t) => t.floor ?? DASH },
                  { key: "p", header: "거래금액", align: "right", cell: (t) => <strong>{manwon(t.priceManwon)}</strong> },
                  { key: "u", header: "m²당(만원)", align: "right", cell: (t) => num(t.pricePerM2) },
                  { key: "s", header: "상태", cell: (t) => <>{t.cancelled && <Badge tone="bad">해제</Badge>} {t.version > 1 && <Badge tone="info">v{t.version}</Badge>} {t.registeredDate && !t.cancelled && <Badge tone="good">등기</Badge>}</> },
                ]} />
              </section>
            </>
          ) : (
            <section className="section">
              <div className="section-head"><h2>매매 거래</h2></div>
              <Empty>매매 거래 없음 — 수집한 매매 실거래에 이 단지의 거래가 없습니다.</Empty>
            </section>
          )}
          {data?.rent && (
            <section className="section">
              <div className="section-head">
                <h2>전월세</h2>
                <span className="sub">{ymLabel(data.rent.window.from)} ~ {ymLabel(data.rent.window.to)} 계약 {num(data.rent.contracts)}건{windowOff ? ` (${PUBLISHED_BASIS})` : ""} · 금액 단위 원 (월세는 월)</span>
              </div>
              <DataTable rows={data.rent.byArea} rowKey={(r) => r.band} caption="전용면적 구간별" empty={rentEmpty} columns={[
                { key: "b", header: "전용면적", cell: (r) => <span className="name">{sqm(r.band)}</span> },
                { key: "j", header: "전세", align: "right", cell: (r) => num(r.jeonse) },
                { key: "jd", header: "전세 중위 보증금", align: "right", cell: (r) => <strong>{manwon(r.jeonseMedianDeposit)}</strong> },
                { key: "w", header: "월세", align: "right", cell: (r) => num(r.wolse) },
                { key: "wr", header: "월세 중위 (보증금 / 월)", align: "right", cell: (r) => (r.wolse ? `${manwon(r.wolseMedianDeposit)} / ${manwon(r.wolseMedianRent)}` : DASH) },
                { key: "t", header: "매매 m²당(만원)", align: "right", cell: (r) => <span>{num(r.tradeMedianPerM2)}<span className="sub">표본 {num(r.tradeSample)}</span></span> },
                { key: "r", header: "전세가율", align: "right", cell: (r) => (r.ratioPct == null ? <span title={ratioOff ? RATIO_INCOMPLETE_NOTE : undefined}>{DASH}</span> : <strong>{num(r.ratioPct, 1)}%</strong>) },
              ]} />
              {ratioOff && <p className="note">전세가율 {DASH}: {ymLabel(data.rent.window.from)} ~ {ymLabel(data.rent.window.to)} {RATIO_INCOMPLETE_NOTE} (계약 수·중앙값은 발행된 계약만으로 낸 값).</p>}
              {data.rent.recent.length > 0 && (
                <DataTable rows={data.rent.recent} rowKey={(r) => r.rentId} caption="최근 계약" columns={[
                  { key: "d", header: "계약일", cell: (r) => r.dealDate.replaceAll("-", ".") },
                  { key: "k", header: "종류", cell: (r) => (r.kind === "jeonse" ? "전세" : "월세") },
                  { key: "a", header: "전용m²", align: "right", cell: (r) => num(r.areaM2, 2) },
                  { key: "f", header: "층", align: "right", cell: (r) => r.floor ?? DASH },
                  { key: "p", header: "보증금 / 월세", align: "right", cell: (r) => <strong>{r.kind === "jeonse" ? manwon(r.depositManwon) : `${manwon(r.depositManwon)} / ${manwon(r.monthlyRentManwon)}`}</strong> },
                  { key: "c", header: "구분", cell: (r) => <>{r.contractType ?? <span className="muted">미상</span>} {r.renewalRightUsed && <Badge tone="info">갱신요구권</Badge>}</> },
                ]} />
              )}
              <div className="note">{data.rent.notes.map((n) => <p key={n}>{n}</p>)}</div>
            </section>
          )}
        </div>
      )}
    </>
  );
}
