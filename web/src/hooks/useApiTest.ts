import { useState } from "react";
import { runProbe, slim } from "../api/probe";
import type { Available, Connectivity } from "../api/types";
import { CONNECTIVITY_PROBE, etagProbe, probes, type Result } from "../domain/apiProbes";
import { ymAdd } from "../lib/format";

/**
 * API 연결 테스트 실행: ① 서버 구성요소(웹 화면 키) ② 공개 API 점검을 순서대로(동시에 보내면 지연이 섞인다)
 * ③ 같은 월 통계 요청을 ETag 로 재검증. 키는 이 Hook 의 메모리에만 (저장·URL 에 쓰지 않음).
 */
export function useApiTest(avail: Available | null, defaultSgg: () => string) {
  const [running, setRunning] = useState(false);
  const [conn, setConn] = useState<Connectivity | null>(null);
  const [connErr, setConnErr] = useState<string | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [at, setAt] = useState<string | null>(null);

  const run = async (key: string) => {
    setRunning(true); setResults([]); setConn(null); setConnErr(null);
    const k = key.trim();
    const c = await runProbe(CONNECTIVITY_PROBE, "");
    if (c.pass) setConn(c.body as Connectivity);
    else setConnErr(c.status == null ? "구성요소 점검 요청이 서버에 닿지 못했습니다 (웹 서버 또는 API 가 꺼져 있음)." : `구성요소 점검 실패: ${c.reason}`);
    const sgg = defaultSgg();
    const to = avail?.default ?? avail?.to ?? ymAdd(new Date().toISOString().slice(0, 7), -3);
    const out: Result[] = [slim(c)];
    let monthsEtag: string | null = null;
    for (const p of probes(sgg, to, k)) {
      const r = await runProbe(p, k);
      if (p.id === "months") monthsEtag = r.etag;
      out.push(slim(r));
      setResults([...out]);
    }
    const months = probes(sgg, to, k).find((p) => p.id === "months");
    if (monthsEtag && months) out.push(slim(await runProbe(etagProbe(months), k, monthsEtag)));
    setResults([...out]);
    setAt(new Date().toISOString());
    setRunning(false);
  };
  return { running, conn, connErr, results, at, run };
}
