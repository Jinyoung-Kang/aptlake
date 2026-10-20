import type { Rent, RentSummary, RentVersion } from "../api/types";
import { paths, type RentQuery } from "../api/endpoints";
import { AREA_BANDS, maxPickDate, versionValue } from "../domain/trades";
import { KINDS, RENT_VERSION_FIELD, rentFilter } from "../domain/rent";
import { useCursorPager } from "../hooks/useCursorPager";
import { useApi, useAvailable } from "../hooks/useApi";
import DateRangePicker from "../components/DateRangePicker";
import RegionPicker from "../components/RegionPicker";
import { DataTable } from "../components/DataTable";
import { Badge, ErrorBox, Group, Kpi, Segmented, Skeleton, Sqm, sqm } from "../components/ui";
import { DASH, isoDate, kst, manwon, num } from "../lib/format";
import { href, setParams, type Route } from "../lib/router";
import { recentRegions, useRegions } from "../lib/regions";

function Detail({ r, onClose }: { r: Rent; onClose: () => void }) {
  const { data, error, stale, loading, reload } = useApi<{ versions: RentVersion[] }>(paths.rentHistory(r.rentId));
  return (
    <div className="panel panel-pad drawer">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <div>
          <div className="muted small">{r.complex.umdName}</div>
          <div style={{ fontWeight: 800, fontSize: 16 }}>{r.complex.aptName}</div>
        </div>
        <button type="button" className="icon-btn" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="quote" style={{ margin: "10px 0 2px" }}>
        <span className="price" style={{ fontSize: 26 }}>{manwon(r.depositManwon)}</span><span className="unit">원 보증금</span>
        {r.kind === "wolse" && <span className="unit">· 월 {manwon(r.monthlyRentManwon)}원</span>}
      </div>
      <div className="quote-sub">{r.dealDate.replaceAll("-", ".")} 계약 · {r.kind === "jeonse" ? "전세" : "월세"} · 전용 {num(r.areaM2, 2)}<Sqm /> · {r.floor ?? DASH}층</div>
      <div className="stat-list one" style={{ marginTop: 10 }}>
        <div className="stat-row"><span className="k">계약 구분</span><span className="v">{r.contractType ?? "미상"}</span></div>
        <div className="stat-row"><span className="k">계약 기간</span><span className="v">{r.contractTerm ?? DASH}</span></div>
        <div className="stat-row"><span className="k">갱신요구권</span><span className="v">{r.renewalRightUsed ?? "미상"}</span></div>
        <div className="stat-row"><span className="k">종전 보증금 / 월세</span><span className="v">{r.previousDepositManwon == null ? DASH : manwon(r.previousDepositManwon)} / {r.previousMonthlyRentManwon == null ? DASH : manwon(r.previousMonthlyRentManwon)}</span></div>
        <div className="stat-row"><span className="k">식별자</span><span className="v"><code>{r.rentId}</code></span></div>
      </div>
      <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>버전 이력 <span className="muted small">원천 값이 바뀔 때마다 새 버전 (SCD2)</span></h3>
      <ErrorBox error={error} onRetry={reload} />
      {!data || stale ? (loading ? <Skeleton h={60} /> : null) : (
        <ul className="timeline">
          {data.versions.map((v) => (
            <li key={v.version}>
              <strong>v{v.version}</strong> {v.current ? <Badge tone="info">현재</Badge> : null}
              <div className="muted small">{kst(v.validFrom, false)} 부터{v.validTo ? ` · ${kst(v.validTo, false)} 까지` : ""}</div>
              {v.version === 1 && v.changes.length === 0 ? <div className="ch">최초 관측</div> : null}
              {v.changes.map((c) => <div key={c.field} className="ch">{RENT_VERSION_FIELD[c.field] ?? c.field}: {versionValue(c.from)} → <strong>{versionValue(c.to)}</strong></div>)}
            </li>
          ))}
        </ul>
      )}
      <p style={{ marginTop: 10 }}><a href={href(`/complex/${r.complex.complexKey}`)}>이 단지 보기 →</a></p>
    </div>
  );
}

export default function RentsPage({ route }: { route: Route }) {
  const avail = useAvailable();
  const { byCode } = useRegions();
  const { sgg, from, to, area, kind, query } = rentFilter(route.params, avail, recentRegions()[0] ?? "41135");
  const today = isoDate(new Date());
  const { rows, summary, cursor, error: err, loading, more, selected: sel, setSelected: setSel } = useCursorPager<Rent, RentSummary>(
    query ? paths.rents(query) : null, (c) => paths.rents({ ...(query as RentQuery), cursor: c }));
  const region = byCode.get(sgg);
  const pending = loading || (!summary && !err);  // 첫 결과·오류 전 = 불러오는 중과 같게 그린다 (QA-013)

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">전월세 · {region?.sidoName ?? ""}</div>
          <h1>{region?.name ?? "전월세"} 전월세 계약</h1>
        </div>
      </div>
      <div className="toolbar">
        <RegionPicker value={sgg} onChange={(c) => setParams(route, { sgg: c })} />
        <DateRangePicker from={from} to={to} min={avail ? `${avail.from}-01` : from} max={avail ? maxPickDate(avail, today) : to}
                         maxDays={366 * 5} onChange={(a, b) => setParams(route, { from: a, to: b })} />
        <Segmented label="종류" value={kind} options={KINDS.map((k) => ({ value: k.key, label: k.label }))} onChange={(v) => setParams(route, { kind: v })} />
        <Group label="전용면적" className="chips">
          {AREA_BANDS.map((a) => <button key={a.key} type="button" className="chip" aria-pressed={a.key === area} onClick={() => setParams(route, { area: a.key })}>{sqm(a.label)}</button>)}
        </Group>
      </div>
      {(summary || pending) && (
        <div className="kpis" aria-busy={!summary}>
          <Kpi k="조회 조건 전체" v={summary ? num(summary.count) : "…"} unit="건" d={summary ? `전세 ${num(summary.jeonse)} · 월세 ${num(summary.wolse)}` : "…"} />
          <Kpi k="전세 중위 보증금" v={summary ? manwon(summary.jeonseMedianDeposit) : "…"} unit="원" d={summary ? `m²당 ${num(summary.jeonseMedianPerM2)}만원 (이상치 제외)` : "…"} />
          <Kpi k="월세 중위 월세" v={summary ? manwon(summary.wolseMedianRent) : "…"} unit="원" d="월세 계약 (월세 0 = 전세)" />
        </div>
      )}
      <ErrorBox error={err} />
      <div className={sel ? "layout" : ""}>
        <div>
          {pending && rows.length === 0 ? <Skeleton h={320} /> : (
            <DataTable
              rows={rows}
              rowKey={(r) => r.rentId}
              selectedKey={sel?.rentId}
              onRowClick={(r) => setSel(r)}
              columns={[
                { key: "date", header: "계약일", cell: (r) => r.dealDate.replaceAll("-", "."), sort: (r) => r.dealDate },
                { key: "apt", header: "단지", cell: (r) => <span className="name">{r.complex.aptName}<span className="sub">{r.complex.umdName}{r.complex.buildYear ? ` · ${r.complex.buildYear}년` : ""}</span></span>, sort: (r) => r.complex.aptName },
                { key: "kind", header: "종류", cell: (r) => (r.kind === "jeonse" ? "전세" : "월세"), sort: (r) => r.kind },
                { key: "area", header: "전용m²", align: "right", cell: (r) => num(r.areaM2, 2), sort: (r) => r.areaM2 },
                { key: "floor", header: "층", align: "right", cell: (r) => r.floor ?? DASH, sort: (r) => r.floor },
                { key: "dep", header: "보증금", align: "right", cell: (r) => <strong>{manwon(r.depositManwon)}</strong>, sort: (r) => r.depositManwon },
                { key: "rent", header: "월세", align: "right", cell: (r) => (r.kind === "wolse" ? manwon(r.monthlyRentManwon) : DASH), sort: (r) => r.monthlyRentManwon },
                { key: "st", header: "구분", cell: (r) => (
                  <span className="chips">
                    {r.contractType ? <Badge tone={r.contractType === "갱신" ? "info" : undefined}>{r.contractType}</Badge> : <span className="muted small">미상</span>}
                    {r.renewalRightUsed && <Badge tone="info" title="갱신요구권 사용">갱신요구권</Badge>}
                    {r.version > 1 && <Badge title="원천 값이 바뀐 적 있음">v{r.version}</Badge>}
                    {r.outlier && <Badge title="월 전국 전세 m²당 보증금 상하위 0.1%">이상치</Badge>}
                    {r.missingSince && <Badge tone="warn" title="최근 수집에서 원천에 없었음">미관측</Badge>}
                  </span>
                ) },
              ]}
              empty="조건에 맞는 계약이 없습니다."
              foot={<>
                <span>{num(rows.length)}건 표시{summary ? ` / 전체 ${num(summary.count)}건` : ""} · 최신 계약일 순 · 행을 누르면 상세·버전 이력</span>
                {cursor ? <button type="button" className="btn" disabled={loading} onClick={more}>{loading ? "불러오는 중…" : "더 보기"}</button> : <span>마지막</span>}
              </>}
            />
          )}
        </div>
        {sel && <aside><Detail r={sel} onClose={() => setSel(null)} /></aside>}
      </div>
      <p className="note">금액 단위: 원 (예: 6억 2,000만). 전세 = 월세가 0 인 계약 — 원천에 전세·월세 구분 항목이 없어 정의로 나눕니다. 계약 구분·갱신요구권은 원천 낱말 그대로이고, 비어 있으면 미상입니다.</p>
      <p className="note"><a href={href(`/region/${sgg}`, { tab: "rent" })}>{region?.name ?? ""} 지역 전월세 추이·전세가율 →</a></p>
    </>
  );
}
