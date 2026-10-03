import type { Trade, TradeVersion } from "../api/types";
import { AREA_BANDS, maxPickDate, tradeFilter, VERSION_FIELD, versionValue } from "../domain/trades";
import { useTradesPager } from "../hooks/useTradesPager";
import { useApi, useAvailable } from "../hooks/useApi";
import DateRangePicker from "../components/DateRangePicker";
import RegionPicker from "../components/RegionPicker";
import { DataTable } from "../components/DataTable";
import { Badge, ErrorBox, Group, Kpi, Skeleton, Sqm, sqm, Switch } from "../components/ui";
import { DASH, isoDate, kst, manwon, num } from "../lib/format";
import { href, setParams, type Route } from "../lib/router";
import { recentRegions, useRegions } from "../lib/regions";
import { paths } from "../api/endpoints";

function Detail({ t, onClose }: { t: Trade; onClose: () => void }) {
  const { data, error, stale, loading, reload } = useApi<{ versions: TradeVersion[] }>(paths.tradeHistory(t.tradeId));
  return (
    <div className="panel panel-pad drawer">
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <div>
          <div className="muted small">{t.complex.umdName}</div>
          <div style={{ fontWeight: 800, fontSize: 16 }}>{t.complex.aptName}</div>
        </div>
        <button type="button" className="icon-btn" aria-label="닫기" onClick={onClose}>✕</button>
      </div>
      <div className="quote" style={{ margin: "10px 0 2px" }}>
        <span className="price" style={{ fontSize: 26 }}>{manwon(t.priceManwon)}</span><span className="unit">원</span>
      </div>
      <div className="quote-sub">{t.dealDate.replaceAll("-", ".")} 계약 · 전용 {num(t.areaM2, 2)}<Sqm /> · {t.floor ?? DASH}층 · <Sqm />당 {num(t.pricePerM2)}만원</div>
      <div className="stat-list one" style={{ marginTop: 10 }}>
        <div className="stat-row"><span className="k">상태</span><span className="v">{t.cancelled ? <Badge tone="bad">해제 {t.cancelDate?.replaceAll("-", ".") ?? ""}</Badge> : "유효"}</span></div>
        <div className="stat-row"><span className="k">등기일</span><span className="v">{t.registeredDate?.replaceAll("-", ".") ?? "미등기"}</span></div>
        <div className="stat-row"><span className="k">거래유형</span><span className="v">{t.dealKind ?? DASH}</span></div>
        <div className="stat-row"><span className="k">매도 → 매수</span><span className="v">{t.sellerType ?? DASH} → {t.buyerType ?? DASH}</span></div>
        <div className="stat-row"><span className="k">식별자</span><span className="v"><code>{t.tradeId}</code></span></div>
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
              {v.changes.map((c) => <div key={c.field} className="ch">{VERSION_FIELD[c.field] ?? c.field}: {versionValue(c.from)} → <strong>{versionValue(c.to)}</strong></div>)}
            </li>
          ))}
        </ul>
      )}
      <p style={{ marginTop: 10 }}><a href={href(`/complex/${t.complex.complexKey}`)}>이 단지의 모든 거래 보기 →</a></p>
    </div>
  );
}

export default function TradesPage({ route }: { route: Route }) {
  const avail = useAvailable();
  const { byCode } = useRegions();
  const { sgg, from, to, area, cancel, query } = tradeFilter(route.params, avail, recentRegions()[0] ?? "41135");
  const today = isoDate(new Date());
  const { rows, summary, cursor, error: err, loading, more, selected: sel, setSelected: setSel } = useTradesPager(query);
  const region = byCode.get(sgg);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumb">거래 목록 · {region?.sidoName ?? ""}</div>
          <h1>{region?.name ?? "거래 목록"} 매매 거래</h1>
        </div>
      </div>
      <div className="toolbar">
        <RegionPicker value={sgg} onChange={(c) => setParams(route, { sgg: c })} />
        {/* 조회 가능 범위가 오기 전에도 같은 자리에 그린다 (그동안은 고른 기간이 곧 범위) — 늦게 나타나면 아래가 밀린다 (QA-013) */}
        <DateRangePicker from={from} to={to} min={avail ? `${avail.from}-01` : from} max={avail ? maxPickDate(avail, today) : to}
                         maxDays={366 * 5} onChange={(a, b) => setParams(route, { from: a, to: b })} />
        <Group label="전용면적" className="chips">
          {AREA_BANDS.map((a) => <button key={a.key} type="button" className="chip" aria-pressed={a.key === area} onClick={() => setParams(route, { area: a.key })}>{sqm(a.label)}</button>)}
        </Group>
        <Switch checked={cancel} onChange={(v) => setParams(route, { cancel: v ? "1" : "0" })} label="해제 거래 포함" />
      </div>
      {/* 불러오는 동안에도 같은 자리를 둔다 — 요약이 늦게 끼어들면 아래 표가 통째로 밀린다 (레이아웃 이동, QA-013) */}
      {(summary || loading) && (
        <div className="kpis" aria-busy={!summary}>
          <Kpi k="조회 조건 전체" v={summary ? num(summary.count) : "…"} unit="건" d={cancel ? `이 중 해제 ${summary ? num(summary.cancelled) : "…"}건` : "해제 제외"} />
          <Kpi k="m²당 중위가" v={summary ? num(summary.medianPpm2) : "…"} unit="만원/m²" d="해제·이상치 제외" />
          <Kpi k="중위 거래가" v={summary ? manwon(summary.medianPrice) : "…"} unit="원" d="해제 제외" />
        </div>
      )}
      <ErrorBox error={err} />
      <div className={sel ? "layout" : ""}>
        <div>
          {loading && rows.length === 0 ? <Skeleton h={320} /> : (
            <DataTable
              rows={rows}
              rowKey={(t) => t.tradeId}
              selectedKey={sel?.tradeId}
              onRowClick={(t) => setSel(t)}
              columns={[
                { key: "date", header: "계약일", cell: (t) => t.dealDate.replaceAll("-", "."), sort: (t) => t.dealDate },
                { key: "apt", header: "단지", cell: (t) => <span className="name">{t.complex.aptName}<span className="sub">{t.complex.umdName}{t.complex.buildYear ? ` · ${t.complex.buildYear}년` : ""}</span></span>, sort: (t) => t.complex.aptName },
                { key: "area", header: "전용m²", align: "right", cell: (t) => num(t.areaM2, 2), sort: (t) => t.areaM2 },
                { key: "floor", header: "층", align: "right", cell: (t) => t.floor ?? DASH, sort: (t) => t.floor },
                { key: "price", header: "거래금액", align: "right", cell: (t) => <strong>{manwon(t.priceManwon)}</strong>, sort: (t) => t.priceManwon },
                { key: "ppm2", header: "m²당(만원)", align: "right", cell: (t) => num(t.pricePerM2), sort: (t) => t.pricePerM2 },
                { key: "st", header: "상태", cell: (t) => (
                  <span className="chips">
                    {t.cancelled && <Badge tone="bad">해제</Badge>}
                    {t.version > 1 && <Badge tone="info" title="원천 값이 바뀐 적 있음">v{t.version}</Badge>}
                    {t.outlier && <Badge title="월 전국 m²당 가격 상하위 0.1%">이상치</Badge>}
                    {t.missingSince && <Badge tone="warn" title="최근 수집에서 원천에 없었음">미관측</Badge>}
                    {t.registeredDate && !t.cancelled && <Badge tone="good">등기</Badge>}
                  </span>
                ) },
              ]}
              empty="조건에 맞는 거래가 없습니다."
              foot={<>
                <span>{num(rows.length)}건 표시{summary ? ` / 전체 ${num(summary.count)}건` : ""} · 최신 계약일 순 · 행을 누르면 상세·버전 이력</span>
                {cursor ? <button type="button" className="btn" disabled={loading} onClick={more}>{loading ? "불러오는 중…" : "더 보기"}</button> : <span>마지막</span>}
              </>}
            />
          )}
        </div>
        {sel && <aside><Detail t={sel} onClose={() => setSel(null)} /></aside>}
      </div>
      {!sel && rows.length > 0 && <p className="note">거래금액 단위: 원 (예: 19억 5,200만). 상태: 해제 · 버전(원천 값 변경) · 이상치 · 미관측 · 등기.</p>}
      <p className="note">다른 지역을 보려면 위 선택기나 상단 검색을 쓰세요. <a href={href(`/region/${sgg}`)}>{region?.name ?? ""} 지역 분석 →</a></p>
    </>
  );
}

