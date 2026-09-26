import { useMemo, useState } from "react";
import { useApi } from "../lib/api";
import { axisX, base, Chart, type Palette } from "../charts/Chart";
import { DataTable } from "../components/DataTable";
import { Badge, CopyButton, ErrorBox, Kpi, Skeleton, StatRow, Switch } from "../components/ui";
import { DASH, kst, num, relative, ymAdd, ymLabel, ymOf } from "../lib/format";
import { setParams, type Route } from "../lib/router";
import { useRegions } from "../lib/regions";

type Summary = {
  partitions: Record<string, number>;
  freshness: { lastFetchedAt: string | null; lastChangedAt: string | null };
  dataset: { version: string; publishedAt: string; dataAsOf: string } | null;
  failedChecks7d: { asset: string; partition: string | null; check: string; severity: string; blocking: boolean; metric: unknown; at: string; resolved: boolean }[];
};
type Rollup = { cells: Record<string, Record<string, Record<string, number>>> };
type Grid = { cells: Record<string, Record<string, [string, number | null]>> };
type Part = {
  partition: { sggCd: string; dealYm: string; status: string; attempts: number; rows: number | null; rowsPrev: number | null; observations: number;
               lastFetchedAt: string | null; lastChangedAt: string | null; nextDueAt: string | null; lastError: string | null };
  checks: { asset: string; name: string; passed: boolean; blocking: boolean; severity: string; metric: unknown; at: string }[];
  lineage: string[];
};

export const STATUS_LABEL: Record<string, string> = {
  MERGED: "반영 완료", LOADED: "적재·반영 대기", PENDING: "미수집", FETCHING: "수집 중", RETRY: "재시도 대기", QUARANTINED: "격리",
};
const STATUS_ORDER = ["MERGED", "LOADED", "FETCHING", "PENDING", "RETRY", "QUARANTINED"];

function statusColor(p: Palette, s: string) {
  return ({ MERGED: p.status.good, LOADED: p.status.info, FETCHING: p.status.info, PENDING: p.status.none, RETRY: p.status.warn, QUARANTINED: p.status.bad } as Record<string, string>)[s] ?? p.status.none;
}

function PartitionDetail({ sgg, ym }: { sgg: string; ym: string }) {
  const { data, error, stale } = useApi<Part>(`/v1/quality/partitions/${sgg}/${ym}`);
  const { byCode } = useRegions();
  if (error) return <ErrorBox error={error} />;
  if (!data || stale) return <Skeleton h={200} />;  // 다른 파티션의 상세가 새 제목 아래 남지 않게
  const p = data.partition;
  return (
    <div className="panel panel-pad">
      <div style={{ fontWeight: 800 }}>{byCode.get(sgg)?.fullName ?? sgg} · {ymLabel(ym)}</div>
      <div className="stat-list one" style={{ marginTop: 6 }}>
        <StatRow k="상태" v={<Badge tone={p.status === "MERGED" ? "good" : p.status === "QUARANTINED" ? "bad" : p.status === "RETRY" ? "warn" : undefined}>{STATUS_LABEL[p.status] ?? p.status}</Badge>} />
        <StatRow k="원천 건수 (이전)" v={`${num(p.rows)} (${num(p.rowsPrev)})`} />
        <StatRow k="관측 횟수 · 재시도" v={`${num(p.observations)}회 · ${p.attempts}회`} />
        <StatRow k="마지막 수집 · 내용 변경" v={`${kst(p.lastFetchedAt, false)} · ${kst(p.lastChangedAt, false)}`} />
        <StatRow k="다음 수집 예정" v={kst(p.nextDueAt, false)} />
      </div>
      {p.lastError && <pre className="log-detail" style={{ padding: 10, marginTop: 8 }}>{p.lastError}</pre>}
      <h3 style={{ fontSize: 13, margin: "12px 0 6px" }}>검사 결과</h3>
      <DataTable rows={data.checks} rowKey={(c) => `${c.asset}:${c.name}`} columns={[
        { key: "n", header: "검사", cell: (c) => <span className="name">{c.name}<span className="sub">{c.asset}</span></span> },
        { key: "r", header: "결과", cell: (c) => c.passed ? <Badge tone="good">통과</Badge> : <Badge tone={c.blocking ? "bad" : "warn"}>{c.blocking ? "실패·차단" : "경고"}</Badge> },
        { key: "m", header: "지표", cell: (c) => <code style={{ whiteSpace: "normal" }}>{JSON.stringify(c.metric)}</code> },
      ]} />
      <h3 style={{ fontSize: 13, margin: "12px 0 6px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        계보 (원본 → 스냅샷 → 발행) <CopyButton text={data.lineage.join("\n")} label="복사" />
      </h3>
      <pre className="code" style={{ fontSize: 11.5 }}>{data.lineage.join("\n") || "아직 발행 전"}</pre>
    </div>
  );
}

export default function QualityPage({ route }: { route: Route }) {
  const { sidos, byCode, sidoName } = useRegions();
  const now = ymOf(new Date());
  const from = ymAdd(now, -71);
  const summary = useApi<Summary>("/v1/quality/summary", { refreshMs: 60_000 });
  const rollup = useApi<Rollup>(`/v1/quality/rollup?from=${from}&to=${now}`);
  const sido = route.params.get("sido");
  const cell = route.params.get("cell"); // 'sgg:YYYYMM'
  const grid = useApi<Grid>(sido ? `/v1/quality/partitions?from=${from}&to=${now}&sido=${sido}` : null);
  const [withResolved, setWithResolved] = useState(false);

  const months = useMemo(() => {
    const set = new Set<string>();
    Object.values(rollup.data?.cells ?? {}).forEach((m) => Object.keys(m).forEach((k) => set.add(k)));
    return [...set].sort();
  }, [rollup.data]);
  const s = summary.data;
  const total = s ? Object.values(s.partitions).reduce((a, b) => a + b, 0) : 0;
  const merged = s?.partitions.MERGED ?? 0;
  const failing = (s?.failedChecks7d ?? []).filter((f) => withResolved || !f.resolved);

  const buildRollup = (p: Palette) => {
    const rows = sidos.map((x) => x.sidoCd);
    const data: [number, number, number, string][] = [];
    rows.forEach((sd, y) => months.forEach((m, x) => {
      const c = rollup.data?.cells[sd]?.[m];
      if (!c) return;
      const tot = Object.values(c).reduce((a, b) => a + b, 0);
      data.push([x, y, Math.round(((c.MERGED ?? 0) / tot) * 100), Object.entries(c).map(([k, v]) => `${STATUS_LABEL[k] ?? k} ${v}`).join(" · ")]);
    }));
    const b = base(p);
    return {
      ...b,
      grid: { left: 8, right: 12, top: 10, bottom: 58, containLabel: true },
      tooltip: { ...b.tooltip, trigger: "item", formatter: (x: { data: [number, number, number, string] }) =>
        `<b>${sidoName(rows[x.data[1]])}</b> · ${ymLabel(`${months[x.data[0]].slice(0, 4)}-${months[x.data[0]].slice(4)}`)}<br/>반영 완료 <b>${x.data[2]}%</b><br/>${x.data[3]}<br/><span style="color:${p.text3}">누르면 시군구별 보기</span>` },
      xAxis: axisX(p, { type: "category", data: months, axisLabel: { color: p.text3, fontSize: 10, formatter: (v: string) => (v.endsWith("01") ? v.slice(0, 4) : ""), interval: 0 }, splitArea: { show: false } }),
      yAxis: { type: "category", data: rows.map(sidoName), inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: p.text2, fontSize: 11 } },
      visualMap: { min: 0, max: 100, dimension: 2, orient: "horizontal", left: "center", bottom: 4, itemWidth: 12, itemHeight: 160,
                   text: ["100% 반영", "0%"], textStyle: { color: p.text2, fontSize: 11 }, inRange: { color: [p.seq[0], p.seq[3], p.seq[6]] }, calculable: false },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: p.surface, borderWidth: 1 }, emphasis: { itemStyle: { borderColor: p.text, borderWidth: 1 } } }],
    };
  };

  const sggRows = useMemo(() => (sido ? (sidos.find((x) => x.sidoCd === sido)?.regions ?? []) : []), [sido, sidos]);
  const buildGrid = (p: Palette) => {
    const data: [number, number, number, number | null][] = [];
    sggRows.forEach((r, y) => months.forEach((m, x) => {
      const c = grid.data?.cells[r.sggCd]?.[m];
      if (c) data.push([x, y, STATUS_ORDER.indexOf(c[0]), c[1]]);
    }));
    const b = base(p);
    return {
      ...b,
      grid: { left: 8, right: 12, top: 10, bottom: 58, containLabel: true },
      tooltip: { ...b.tooltip, trigger: "item", formatter: (x: { data: [number, number, number, number | null] }) =>
        `<b>${sggRows[x.data[1]].name}</b> · ${months[x.data[0]]}<br/>${STATUS_LABEL[STATUS_ORDER[x.data[2]]]} · 원천 ${x.data[3] ?? DASH}건<br/><span style="color:${p.text3}">누르면 파티션 상세</span>` },
      xAxis: axisX(p, { type: "category", data: months, axisLabel: { color: p.text3, fontSize: 10, formatter: (v: string) => (v.endsWith("01") ? v.slice(0, 4) : ""), interval: 0 } }),
      yAxis: { type: "category", data: sggRows.map((r) => r.name), inverse: true, axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: p.text2, fontSize: 11 } },
      visualMap: { type: "piecewise", dimension: 2, orient: "horizontal", left: "center", bottom: 4, textStyle: { color: p.text2, fontSize: 11 },
                   pieces: STATUS_ORDER.map((st, i) => ({ value: i, label: STATUS_LABEL[st], color: statusColor(p, st) })) },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: p.surface, borderWidth: 1 } }],
    };
  };

  return (
    <>
      <div className="page-head">
        <div><div className="crumb">데이터 품질</div><h1>수집 완결도와 품질 검사</h1></div>
      </div>
      <ErrorBox error={summary.error} />
      {s ? (
        <div className="kpis">
          <Kpi k="최근 원천 수집" v={relative(s.freshness.lastFetchedAt)} d={`${kst(s.freshness.lastFetchedAt, false)} · 내용 변경 ${relative(s.freshness.lastChangedAt)}`} />
          <Kpi k="데이터셋 버전" v={<span style={{ fontSize: 17 }}>{s.dataset?.version ?? DASH}</span>} d={`발행 ${kst(s.dataset?.publishedAt, false)}`} />
          <Kpi k="반영 완료 파티션 (시군구×월)" v={`${num(total ? (merged / total) * 100 : 0, 1)}%`}
               d={<>{num(merged)} / {num(total)} · 격리 {num(s.partitions.QUARANTINED ?? 0)} · 재시도 {num(s.partitions.RETRY ?? 0)}<div className="progress"><span style={{ width: `${total ? (merged / total) * 100 : 0}%` }} /></div></>} />
          <Kpi k="현재 실패 중인 검사" v={num((s.failedChecks7d ?? []).filter((f) => !f.resolved).length)} unit="건" d="최근 7일, 이후 통과한 검사는 제외" />
        </div>
      ) : <Skeleton h={90} />}

      <section className="section">
        <div className="section-head">
          <h2>시도별 수집 완결도</h2><span className="sub">칸 = 시도 × 계약월, 색 = 시군구 중 반영 완료 비율 · 칸을 누르면 시군구별</span>
        </div>
        <ErrorBox error={rollup.error} />
        <div className="panel panel-pad">
          {rollup.data && sidos.length ? (
            <Chart build={buildRollup} deps={[rollup.data, sidos, months]} height={Math.max(360, sidos.length * 22 + 80)} label="시도별 월별 반영 완료 비율 히트맵"
                   onClick={(e) => { const d = e.data as [number, number] | undefined; if (d) setParams(route, { sido: sidos[d[1]].sidoCd, cell: null }); }} />
          ) : <Skeleton h={360} />}
        </div>
      </section>

      {sido && (
        <section className="section">
          <div className="section-head">
            <h2>{sidoName(sido)} 시군구별 상태</h2><span className="sub">칸을 누르면 파티션 상세 (검사·계보·원본 경로)</span>
            <div className="tools"><button type="button" className="btn" onClick={() => setParams(route, { sido: null, cell: null })}>닫기</button></div>
          </div>
          <div className={cell ? "layout wide-aside" : ""}>
            <div className="panel panel-pad">
              {grid.data && !grid.stale ? (
                <Chart build={buildGrid} deps={[grid.data, sggRows, months]} height={Math.max(260, sggRows.length * 20 + 80)} label={`${sidoName(sido)} 시군구별 수집 상태`}
                       onClick={(e) => { const d = e.data as [number, number] | undefined; if (d) setParams(route, { cell: `${sggRows[d[1]].sggCd}:${months[d[0]]}` }); }} />
              ) : grid.error ? <ErrorBox error={grid.error} /> : <Skeleton h={260} />}
            </div>
            {cell && (() => {
              const [code, m] = cell.split(":");
              return byCode.has(code) && /^\d{6}$/.test(m) ? <aside><PartitionDetail sgg={code} ym={`${m.slice(0, 4)}-${m.slice(4)}`} /></aside> : null;
            })()}
          </div>
        </section>
      )}

      <section className="section">
        <div className="section-head">
          <h2>{withResolved ? "최근 7일 품질 검사 실패" : "현재 실패 중인 품질 검사"}</h2>
          <span className="sub">차단 = 하위 반영·발행을 멈춤 · 경고 = 기록만</span>
          <div className="tools"><Switch checked={withResolved} onChange={setWithResolved} label="이후 통과한 항목 포함" /></div>
        </div>
        <DataTable rows={failing} rowKey={(f) => `${f.asset}:${f.partition}:${f.check}:${f.at}`} empty="실패 중인 검사가 없습니다." maxHeight={420}
          columns={[
            { key: "at", header: "시각", cell: (f) => kst(f.at, false), sort: (f) => f.at },
            { key: "asset", header: "자산 · 파티션", cell: (f) => <span className="name">{f.asset}<span className="sub">{f.partition ?? DASH}</span></span> },
            { key: "check", header: "검사", cell: (f) => f.check },
            { key: "sev", header: "등급", cell: (f) => <>{f.blocking ? <Badge tone="bad">차단</Badge> : <Badge tone="warn">경고</Badge>} {f.resolved && <Badge tone="good">이후 통과</Badge>}</> },
            { key: "m", header: "지표", cell: (f) => <code style={{ whiteSpace: "normal" }}>{JSON.stringify(f.metric).slice(0, 140)}</code> },
          ]} />
        <p className="note">자세한 오류 원문과 전체 복사는 <a href="#/ops">수집 상태</a> 메뉴의 오류 로그에서 볼 수 있습니다.</p>
      </section>
    </>
  );
}
