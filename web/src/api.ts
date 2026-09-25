// 공개 API 클라이언트. API 키는 개발자 화면에서만 쓰고, 메모리(React state)에만 둔다 — 저장소에 쓰지 않는다.
export type Problem = { status: number; code: string; title: string; detail?: string; traceId?: string };

export class ApiError extends Error {
  constructor(public problem: Problem) {
    super(problem.detail || problem.title);
  }
}

export type Meta = { datasetVersion?: string; dataAsOf?: string | null };

export async function api<T>(path: string, key?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, { headers });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(body ?? { status: res.status, code: "HTTP_ERROR", title: res.statusText });
  }
  return body as T;
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const p = e.problem;
    if (p.status === 429) return `요청 한도 초과 (${p.code}) — 잠시 후 다시 시도하세요.`;
    return `${p.title} (${p.code})${p.detail ? `: ${p.detail}` : ""}`;
  }
  return String(e);
}

export type Region = { sggCd: string; sidoCd: string; sidoName: string; name: string; fullName: string };

export function ym(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function addMonths(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + n, 1);
}
