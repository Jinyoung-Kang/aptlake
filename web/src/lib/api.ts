import { useEffect, useState } from "react";

// 공개 API 클라이언트. 웹 화면은 BFF(nginx)가 서버 쪽 키를 붙이므로 브라우저에는 키가 없다.
// 개발자 화면에서 사용자가 넣은 키만 헤더로 보낸다 (메모리에만 보관).
export type Problem = { status: number; code: string; title: string; detail?: string; traceId?: string };

export class ApiError extends Error {
  constructor(public problem: Problem) {
    super(problem.detail || problem.title);
  }
}

const cache = new Map<string, { at: number; data: unknown }>();
const TTL_MS = 60_000;

// 일시 장애(DB 과부하·재시작, 프록시 연결 실패)는 잠깐 뒤 다시 하면 대개 성공한다 → 최대 2번, 서버가 준 Retry-After(최대 3초) 존중
const RETRYABLE = new Set([502, 503, 504]);
const MAX_RETRIES = 2;
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("Aborted", "AbortError")); }, { once: true });
  });
}

/** fetch + 일시 오류 재시도. 429(한도)·4xx·500(버그)은 재시도하지 않는다. */
export async function fetchRetry(path: string, init: RequestInit): Promise<Response> {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(path, init);
      if (!RETRYABLE.has(res.status) || attempt >= MAX_RETRIES) return res;
      const ra = Number(res.headers.get("retry-after"));
      await sleep(Math.min(ra > 0 ? ra * 1000 : 700 * (attempt + 1), 3000), init.signal ?? undefined);
    } catch (e) {
      if (init.signal?.aborted || attempt >= MAX_RETRIES) throw e;
      await sleep(700 * (attempt + 1), init.signal ?? undefined);  // 네트워크 오류(연결 거부 등)
    }
  }
}

export async function api<T>(path: string, opts: { key?: string; signal?: AbortSignal; fresh?: boolean } = {}): Promise<T> {
  const hit = !opts.key && !opts.fresh ? cache.get(path) : undefined;
  if (hit && Date.now() - hit.at < TTL_MS) return hit.data as T;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.key) headers["X-API-Key"] = opts.key;
  const res = await fetchRetry(path, { headers, signal: opts.signal });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.code ? body : { status: res.status, code: `HTTP_${res.status}`, title: res.statusText || "HTTP 오류" });
  if (!opts.key) cache.set(path, { at: Date.now(), data: body });
  return body as T;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const p = e.problem;
    const trace = p.traceId ? ` · 추적 ID ${p.traceId.slice(0, 12)}` : "";
    if (p.status === 429) return `요청 한도를 넘었습니다 (${p.code}). 잠시 후 다시 시도하세요.`;
    if (p.status === 503 || p.status === 502 || p.status === 504)
      return `${p.detail ?? "서버가 일시적으로 응답하지 못했습니다."} 자동으로 ${MAX_RETRIES}번 다시 시도했지만 실패했습니다 (${p.code}${trace}).`;
    if (p.status >= 500) return `서버 내부 오류로 불러오지 못했습니다 (${p.code}${trace}). 수집 상태 메뉴의 오류 로그에서 추적 ID 로 찾을 수 있습니다.`;
    return `${p.title}${p.detail ? ` — ${p.detail}` : ""} (${p.code})`;
  }
  if (e instanceof DOMException && e.name === "AbortError") return "";
  return "서버에 연결할 수 없습니다 (자동 재시도 후에도 실패). 웹 서버·API 컨테이너가 켜져 있는지 확인하세요.";
}

export type Loadable<T> = {
  data: T | null; error: string | null; loading: boolean;
  /** data 가 이전 path 의 결과(새 path 를 불러오는 중) — 화면은 흐리게 유지하고 비우지 않는다 */
  stale: boolean;
  reload: () => void;
};

/**
 * path 가 바뀌면 이전 요청을 취소하고 다시 부른다. path=null 이면 부르지 않음.
 * 불러오는 동안 이전 결과를 그대로 두고 stale=true 로 알린다 (조건을 바꿀 때마다 화면이 비었다 다시 그려지는 깜빡임 방지).
 * 새 path 가 실패하면 이전 결과는 버린다 — 다른 조건의 값이 오류 옆에 남아 있지 않도록.
 */
export function useApi<T>(path: string | null, opts: { fresh?: boolean; refreshMs?: number } = {}): Loadable<T> {
  const [state, setState] = useState<{ data: T | null; of: string | null; error: string | null; loading: boolean }>({
    data: null, of: null, error: null, loading: !!path,
  });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) {
      setState({ data: null, of: null, error: null, loading: false });
      return;
    }
    const ctrl = new AbortController();
    setState((s) => ({ ...s, error: null, loading: true }));
    api<T>(path, { signal: ctrl.signal, fresh: opts.fresh || tick > 0 })
      .then((data) => setState({ data, of: path, error: null, loading: false }))
      .catch((e) => {
        if (ctrl.signal.aborted) return;
        setState((s) => (s.of === path ? { ...s, error: errorText(e), loading: false } : { data: null, of: null, error: errorText(e), loading: false }));
      });
    return () => ctrl.abort();
  }, [path, tick, opts.fresh]);
  useEffect(() => {
    if (!opts.refreshMs || !path) return;
    const t = setInterval(() => setTick((x) => x + 1), opts.refreshMs);
    return () => clearInterval(t);
  }, [opts.refreshMs, path]);
  const stale = state.data != null && state.of !== path;
  return { data: state.data, error: state.error, loading: state.loading || stale, stale, reload: () => setTick((x) => x + 1) };
}

// ───── 응답 타입 ─────
export type Region = { sggCd: string; sidoCd: string; sidoName: string; name: string; fullName: string };

export type Trade = {
  tradeId: string; dealDate: string; priceManwon: number; areaM2: number; floor: number | null;
  pricePerM2: number | null; cancelled: boolean; cancelDate: string | null; registeredDate: string | null;
  aptDong: string | null; dealKind: string | null; sellerType: string | null; buyerType: string | null;
  outlier: boolean; version: number; missingSince: string | null;
  complex: { complexKey: string; aptName: string; umdName: string; jibun: string | null; buildYear: number | null };
};

export type MonthRow = {
  dealYm: string; reported: number; trades: number; cancelled: number; sampleSize: number; outliers: number;
  p25PricePerM2: number | null; medianPricePerM2: number | null; p75PricePerM2: number | null;
  provisional?: boolean; lowSample?: boolean;
};

export type AreaSummary = {
  regionId: string; trades: number; cancelled: number; cancelRate: number | null; median: number | null;
  p25: number | null; p75: number | null; sample: number; tradesMoM: number | null; tradesYoY: number | null;
  medianYoY: number | null; spark: { months: string[]; trades: number[]; median: (number | null)[] };
};

export type SggSummary = {
  sggCd: string; trades: number; cancelled: number; cancelRate: number | null; median: number | null;
  sample: number; lowSample: boolean; medianYoY: number | null; tradesYoY: number | null; tradesMoM: number | null;
};

export type Overview = {
  month: string; provisional: boolean; available: { from: string; to: string; default: string };
  nation: AreaSummary | null; sido: AreaSummary[]; sgg: SggSummary[];
  rankings: { volume: SggSummary[]; gainers: SggSummary[]; losers: SggSummary[] };
  definitions: Record<string, string>; disclaimer: string; datasetVersion: string; dataAsOf: string | null;
};

export type Available = { from: string; to: string; default: string };
export type Ticker = { month: string; items: TickerItem[]; available: Available | null; datasetVersion: string; dataAsOf: string | null };

export type TickerItem = {
  key: string; label: string; value: number | null; unit: string; change: number | null;
  changeBasis: string | null; provisional?: boolean;
};

type IndexPoint = { period: string; value: number; provisional: boolean; mom: number | null; yoy: number | null };
/** /v1/index/summary 항목 — 최상위 값은 가장 최근 달(잠정일 수 있음), confirmed 는 잠정이 아닌 가장 최근 달(대표값). */
export type IndexSummaryItem = IndexPoint & {
  regionId: string; confirmed: IndexPoint | null; spark: number[];
  corrMoM: number | null; directionMatch: number | null; months: number;
};

/** 조회 가능한 계약월 범위(발행된 데이터 기준) — 시세 띠 응답을 같이 쓴다 (클라이언트 캐시 60초). */
export function useAvailable(): Available | null {
  const { data } = useApi<Ticker>("/v1/market/ticker");
  return data?.available ?? null;
}

