import { useMemo, useState } from "react";
import { useApi } from "../hooks/useApi";
import ApiTest from "../components/ApiTest";
import { DataTable } from "../components/DataTable";
import { Badge, CopyButton, ErrorBox, Kpi, Segmented, Skeleton, Switch, Tabs } from "../components/ui";
import { DASH, kst, kstShort, num, relative, ymLabel } from "../lib/format";
import { setParams, type Route } from "../lib/router";
import { paths } from "../api/endpoints";
import type { OpsErrors, OpsJob, OpsStatus } from "../api/types";
import { entryState, entryText, filterEntries, JOB_STATUS, jobDuration, LOG_SOURCES, logFileName, logText, pipelineSummary, PRIORITY_TONE, queueOrder, recentOrder, SOURCE_LABEL } from "../domain/ops";
import { useLogClear } from "../hooks/useLogClear";
import { useNow } from "../hooks/useNow";
import { useToggleSet } from "../hooks/useToggleSet";
import { saveText } from "../lib/download";

function StatusCell({ s }: { s: string }) {
  const st = JOB_STATUS[s] ?? { label: s, cls: "" };
  return <span className={`status ${st.cls}`}><span className="dot" aria-hidden="true" />{st.label}</span>;
}

function JobsTable({ jobs, now, empty }: { jobs: OpsJob[]; now: number; empty: string }) {
  return (
    <DataTable rows={jobs} rowKey={(j) => j.runId} empty={empty} maxHeight={520}
      columns={[
        { key: "job", header: "작업", cell: (j) => (
          <span className="name">{j.jobLabel}{j.partition ? ` · ${ymLabel(`${j.partition.slice(0, 4)}-${j.partition.slice(4)}`)}` : ""}
            <span className="sub">
              <Badge tone={PRIORITY_TONE[j.priority ?? ""]}>{j.priorityLabel}</Badge> {j.trigger} · <code title="Dagster 실행 ID">{j.runId.slice(0, 8)}</code>
            </span>
          </span>
        ), sort: (j) => j.jobLabel },
        { key: "status", header: "상태", cell: (j) => <StatusCell s={j.status} />, sort: (j) => j.status },
        { key: "req", header: "요청", cell: (j) => <span title={kst(j.requestedAt)}>{kstShort(j.requestedAt)}<span className="sub">{relative(j.requestedAt, now)}</span></span>, sort: (j) => j.requestedAt },
        { key: "start", header: "시작", cell: (j) => <span title={kst(j.startedAt)}>{kstShort(j.startedAt)}</span>, sort: (j) => j.startedAt },
        { key: "dur", header: "소요", align: "right", cell: (j) => jobDuration(j, now), sort: (j) => j.durationS },
      ]} />
  );
}

export default function OpsPage({ route }: { route: Route }) {
  const [auto, setAuto] = useState(true);
  const status = useApi<OpsStatus>(paths.opsStatus(), { refreshMs: auto ? 30_000 : undefined });
  const hours = route.params.get("hours") ?? "24";
  const source = route.params.get("source") ?? "all";
  const resolved = route.params.get("resolved") === "1";
  const showCleared = route.params.get("old") === "1";
  const errs = useApi<OpsErrors>(paths.opsErrors(hours, resolved, showCleared), { refreshMs: auto ? 30_000 : undefined });
  const clear = useLogClear(errs.reload);
  const [tab, setTab] = useState<"active" | "recent" | "schedules">("active");
  const [q, setQ] = useState("");
  const open = useToggleSet();
  const now = useNow();

  const s = status.data;
  const b = s?.summary.budget;
  const { running, queued, mergedPct } = pipelineSummary(s);
  const entries = useMemo(() => filterEntries(errs.data?.entries ?? [], source, q), [errs.data, source, q]);
  const allText = () => logText(entries, { hours, source, q, nowIso: new Date().toISOString() });
  const download = () => saveText(logFileName(new Date().toISOString()), `${allText()}\n`);

  return (
    <>
      <div className="page-head">
        <div><div className="crumb">수집 상태 · 운영</div><h1>파이프라인 작업과 오류 로그</h1></div>
        <div className="tools">
          <Switch checked={auto} onChange={setAuto} label="30초마다 새로고침" />
          <button type="button" className="btn" onClick={() => { status.reload(); errs.reload(); }}>새로고침</button>
          <span className="muted small">서버 시각 {kst(s?.serverTime, true)}</span>
        </div>
      </div>
      <ErrorBox error={status.error} onRetry={status.reload} />
      {s && !s.pipeline.available && <p className="error">{s.pipeline.reason}</p>}
      {s ? (
        <div className="kpis">
          <Kpi k="파이프라인" v={s.pipeline.available ? `${running} 실행 · ${queued} 대기` : "연결 안 됨"} d={`최근 48시간 ${num(s.jobs.recent.length)}건 완료·실패`} />
          <Kpi k={`오늘 원천 호출 (${b?.day ?? ""})`} v={`${num(b?.used)} / ${num(b?.cap)}`}
               d={<>{b?.exhaustedReason ? <Badge tone="warn" title={b.stopLabel ?? b.exhaustedReason}>{b.exhaustedReason.startsWith("KeyRejected") ? "인증키 거부 · 키 확인 필요" : "원천 한도 소진 · 자정 뒤 재개"}</Badge> : `일 한도 ${num(b?.limit)}의 80%`}
                   <div className={`progress ${b && b.used >= b.cap ? "warn" : ""}`}><span style={{ width: `${b ? Math.min(100, (b.used / b.cap) * 100) : 0}%` }} /></div></>} />
          <Kpi k="수집 진행 (시군구×월)" v={`${num(mergedPct, 1)}%`}
               d={<>남은 {num(s.summary.backfillEstimate.remainingCalls)}개 · 추정 {s.summary.backfillEstimate.days ?? DASH}일<div className="progress"><span style={{ width: `${mergedPct}%` }} /></div></>}
               title={`추정 근거: ${s.summary.backfillEstimate.basis}`} />
          <Kpi k="마지막 발행" v={<span style={{ fontSize: 16 }}>{s.summary.lastPublish?.version ?? DASH}</span>}
               d={`${relative(s.summary.lastPublish?.at)} · 발행 대기 ${num(s.summary.publishPending)}개월`} />
        </div>
      ) : status.loading ? <Skeleton h={90} /> : null}

      <section className="section">
        <div className="section-head"><h2>작업 큐 · 스케줄</h2><span className="sub">Dagster 실행 기록 (읽기 전용)</span></div>
        <Tabs value={tab} onChange={setTab} options={[
          { value: "active", label: "진행·대기", n: s?.jobs.active.length ?? 0 },
          { value: "recent", label: "최근 48시간", n: s?.jobs.recent.length ?? 0 },
          { value: "schedules", label: "스케줄·센서", n: s?.schedules.length ?? 0 },
        ]} />
        {!s ? (status.loading ? <Skeleton h={200} /> : <div className="empty">수집 상태를 불러오지 못했습니다. 위의 다시 시도를 누르세요.</div>) : tab === "schedules" ? (
          <DataTable rows={s.schedules} rowKey={(x) => x.name} columns={[
            { key: "n", header: "작업", cell: (x) => <span className="name">{x.label}<span className="sub">{x.type === "sensor" ? "센서" : "스케줄"} · {x.name}</span></span> },
            { key: "r", header: "규칙", cell: (x) => x.rule },
            { key: "s", header: "상태", cell: (x) => (x.status === "RUNNING" ? <Badge tone="good">켜짐</Badge> : <Badge>꺼짐</Badge>) },
            { key: "next", header: "다음 실행", cell: (x) => <span title={kst(x.nextAt)}>{kstShort(x.nextAt)}<span className="sub">{relative(x.nextAt, now)}</span></span> },
            { key: "last", header: "마지막 평가", cell: (x) => x.lastTick ? (
              <span>{x.lastTick.status === "SUCCESS" ? `실행 ${x.lastTick.runs}건 요청` : x.lastTick.status === "SKIPPED" ? "건너뜀" : x.lastTick.status === "FAILURE" ? "오류" : x.lastTick.status}
                <span className="sub">{kstShort(x.lastTick.at)} · {x.lastTick.error ?? x.lastTick.skipReason ?? ""}</span></span>
            ) : DASH },
          ]} />
        ) : tab === "active" ? (
          <JobsTable jobs={queueOrder(s.jobs.active)} now={now} empty="진행·대기 중인 작업이 없습니다. (원천 호출 예산을 다 썼거나 기한이 된 파티션이 없으면 센서가 실행을 만들지 않습니다)" />
        ) : (
          <JobsTable jobs={recentOrder(s.jobs.recent)} now={now} empty="최근 48시간 기록이 없습니다." />
        )}
      </section>

      <ApiTest />

      <section className="section">
        <div className="section-head">
          <h2>오류 로그</h2><span className="sub">파이프라인 실행 · 원천 수집 · 품질 검사 · API 5xx — 비밀값은 가려서 표시</span>
        </div>
        <div className="toolbar">
          <Segmented label="기간" value={hours} onChange={(v) => setParams(route, { hours: v })}
                     options={[{ value: "24", label: "24시간" }, { value: "168", label: "7일" }, { value: "720", label: "30일" }]} />
          <div className="chips" role="group" aria-label="출처">
            {LOG_SOURCES.map((k) => (
              <button key={k} type="button" className="chip" aria-pressed={source === k} onClick={() => setParams(route, { source: k })}>
                {k === "all" ? "전체" : SOURCE_LABEL[k]}<span className="n">{k === "all" ? (errs.data?.entries.length ?? 0) : (errs.data?.counts[k] ?? 0)}</span>
              </button>
            ))}
          </div>
          <input className="text-input" style={{ minWidth: 200 }} placeholder="로그 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="로그 검색" />
          <Switch checked={resolved} onChange={(v) => setParams(route, { resolved: v ? "1" : null })} label="해결된 항목 포함" />
          {errs.data?.cleared && <Switch checked={showCleared} onChange={(v) => setParams(route, { old: v ? "1" : null })} label="비우기 이전 보기" />}
          <div className="tools-right">
            {clear.confirming ? (
              <span className="confirm" role="group" aria-label="로그 비우기 확인">
                <span className="small">지금까지의 로그를 숨길까요?</span>
                <button type="button" className="btn danger" onClick={() => clear.setCleared(true)}>비우기</button>
                <button type="button" className="btn" onClick={() => clear.setConfirming(false)}>취소</button>
              </span>
            ) : (
              <button type="button" className="btn" onClick={() => clear.setConfirming(true)} title="원본 기록은 지우지 않고, 지금 이전 항목을 화면에서 숨깁니다">로그 비우기</button>
            )}
            <button type="button" className="btn" onClick={() => open.set(open.size ? [] : entries.map((e) => e.id))}>{open.size ? "모두 접기" : "모두 펼치기"}</button>
            <CopyButton text={allText} label={`전체 복사 (${entries.length})`} className="btn primary" disabled={errs.stale || !entries.length} />
            <button type="button" className="btn" onClick={download} disabled={errs.stale || !entries.length}>.txt 저장</button>
          </div>
        </div>
        <ErrorBox error={errs.error} onRetry={errs.reload} />
        <ErrorBox error={clear.error} />
        {errs.data?.cleared && (
          <div className="banner">
            <span><b>{kst(errs.data.cleared.at)}</b>에 로그를 비웠습니다 — 그 이전 항목은 숨김 (원본 기록은 보존). 이후 새로 생긴 오류만 표시합니다.</span>
            <button type="button" className="btn ghost" onClick={() => clear.setCleared(false)}>되돌리기</button>
          </div>
        )}
        {errs.data?.notes.filter((n) => !n.startsWith("비우기 이전")).map((n) => <p key={n} className="note">{n}</p>)}
        <div data-stale={errs.stale} aria-busy={errs.loading}>
        {!errs.data ? (errs.loading ? <Skeleton h={200} /> : null) : entries.length === 0 ? (
          <div className="log"><div className="empty">이 기간·조건에 오류가 없습니다.</div></div>
        ) : (
          <div className="log" role="list">
            {entries.map((e) => (
              <div key={e.id} className={`log-row ${e.resolved || e.cleared ? "resolved" : ""}`} role="listitem">
                <div className="log-head" onClick={() => open.toggle(e.id)} aria-expanded={open.has(e.id)}>
                  <span className="at">{kst(e.at)}</span>
                  <span className={`lvl ${e.level}`}>{e.level}</span>
                  <span className="src">{SOURCE_LABEL[e.source] ?? e.source}</span>
                  <span className="msg"><Badge tone={entryState(e).tone}>{entryState(e).label}</Badge> {e.message}<span className="where">{e.where}</span></span>
                  <CopyButton text={() => entryText(e)} label="복사" className="btn ghost" />
                </div>
                {open.has(e.id) && e.detail ? <pre className="log-detail">{e.detail}</pre> : null}
              </div>
            ))}
          </div>
        )}
        {errs.data?.truncated && <p className="note">최근 500건까지만 표시합니다. 기간을 줄이거나 출처를 고르세요.</p>}
        {errs.data && errs.data.apiErrorSummary.length > 0 && (
          <>
            <h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>API 오류 응답 요약 <span className="muted small">4xx·5xx, 같은 기간</span></h3>
            <DataTable rows={errs.data.apiErrorSummary} rowKey={(r) => `${r.route}:${r.status}`} columns={[
              { key: "r", header: "경로", cell: (r) => <code>{r.route}</code> },
              { key: "s", header: "상태", align: "right", cell: (r) => <Badge tone={r.status >= 500 ? "bad" : r.status === 429 ? "warn" : undefined}>{r.status}</Badge> },
              { key: "c", header: "건수", align: "right", cell: (r) => num(r.count), sort: (r) => r.count },
            ]} />
            <p className="note">4xx 는 대부분 정상적인 거절(잘못된 매개변수·한도 초과·인증 실패)입니다. 5xx 만 로그에 개별 항목으로 남깁니다.</p>
          </>
        )}
        </div>
      </section>
    </>
  );
}
