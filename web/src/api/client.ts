// 공개 API 클라이언트: 요청·일시 오류 재시도·조회 캐시·오류 문구. React 에 의존하지 않는다 (상태·부수효과는 hooks/useApi).
// 웹 화면은 BFF(nginx)가 서버 쪽 키를 붙이므로 브라우저에는 키가 없다. 개발자 화면에서 사용자가 넣은 키만 헤더로 보낸다 (메모리에만 보관).
export type Problem = { status: number; code: string; title: string; detail?: string; traceId?: string };

export class ApiError extends Error {
  constructor(public problem: Problem) {
    super(problem.detail || problem.title);
  }
}

const cache = new Map<string, { at: number; data: unknown }>();
const TTL_MS = 60_000;
// 탭을 오래 열어 두고 조건·페이지를 계속 바꿔도 메모리가 늘지 않도록 항목 수를 묶는다 (삽입 순서 = 오래된 순)
const CACHE_MAX = 64;
function remember(path: string, data: unknown): void {
  const now = Date.now();
  cache.delete(path);
  for (const [k, v] of cache) if (now - v.at >= TTL_MS) cache.delete(k);
  while (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value as string);
  cache.set(path, { at: now, data });
}

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

// 응답이 오기 전에 같은 조회가 또 오면 진행 중인 요청을 함께 쓴다 (예: 시세 띠를 App·Shell 이 동시에 부름 — QA-012)
const inflight = new Map<string, Promise<unknown>>();

export async function api<T>(path: string, opts: { key?: string; signal?: AbortSignal; fresh?: boolean } = {}): Promise<T> {
  const shared = !opts.key && !opts.fresh;
  const hit = shared ? cache.get(path) : undefined;
  if (hit && Date.now() - hit.at < TTL_MS) return hit.data as T;
  if (!shared) return load<T>(path, opts);
  let p = inflight.get(path);
  if (!p) {
    // 함께 쓰는 요청은 한 호출자의 취소로 끊지 않는다 — 각 호출자는 자기 signal 로만 기다림을 그만둔다
    p = load(path, {}).finally(() => inflight.delete(path));
    inflight.set(path, p);
  }
  return (await untilAborted(p, opts.signal)) as T;
}

async function load<T>(path: string, opts: { key?: string; signal?: AbortSignal }): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.key) headers["X-API-Key"] = opts.key;
  const res = await fetchRetry(path, { headers, signal: opts.signal });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.code ? body : { status: res.status, code: `HTTP_${res.status}`, title: res.statusText || "HTTP 오류" });
  if (!opts.key) remember(path, body);
  return body as T;
}

function untilAborted<T>(p: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return p;
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", onAbort, { once: true });
    p.then(
      (v) => { signal.removeEventListener("abort", onAbort); resolve(v); },
      (e) => { signal.removeEventListener("abort", onAbort); reject(e); },
    );
  });
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

/** 쓰기 요청(POST·DELETE). 자동 재시도하지 않는다 — 사용자가 누른 동작은 결과를 그대로 보여 준다. 성공하면 조회 캐시를 비운다.
 * key: 사용자가 넣은 키(예: 로그 비우기의 운영자 키) — 이 요청에만 붙는다. BFF 는 사용자가 넣은 키를 그대로 넘긴다. */
export async function apiSend<T>(method: "POST" | "DELETE", path: string, key?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { method, headers });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.code ? body : { status: res.status, code: `HTTP_${res.status}`, title: res.statusText || "HTTP 오류" });
  cache.clear();
  inflight.clear();
  return body as T;
}
