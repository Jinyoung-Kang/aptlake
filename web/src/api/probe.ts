// API 연결 테스트의 실제 요청 하나 — 캐시 없이 보내고 상태·지연·헤더를 잰다.
import type { Probe, Result } from "../domain/apiProbes";

export async function runProbe(p: Probe, key: string, etag?: string | null): Promise<Result & { etag: string | null; body: unknown }> {
  const headers: Record<string, string> = { Accept: "application/json", ...(key && !p.headers ? { "X-API-Key": key } : {}), ...(p.headers ?? {}) };
  if (etag) headers["If-None-Match"] = etag;
  const t0 = performance.now();
  const base = { id: p.id, name: p.name, path: p.path, expect: p.expect };
  try {
    const r = await fetch(p.path, { headers, cache: "no-store" });
    const text = await r.text();
    const ms = performance.now() - t0;
    let body: unknown = null;
    try { body = text ? JSON.parse(text) : null; } catch { /* JSON 이 아닌 본문(프록시 오류 페이지 등) */ }
    const reason = p.check(r, body);
    return {
      ...base, status: r.status, ms, pass: reason == null, reason: reason ?? "통과",
      cache: r.headers.get("x-cache"), version: r.headers.get("x-dataset-version"),
      remaining: r.headers.get("x-ratelimit-remaining"), limit: r.headers.get("x-ratelimit-limit"),
      bytes: text.length, etag: r.headers.get("etag"), body,
    };
  } catch (e) {
    return { ...base, status: null, ms: null, pass: false, reason: `연결 실패 (${(e as Error).name})`, cache: null, version: null, remaining: null, limit: null, bytes: null, etag: null, body: null };
  }
}

/** 표에는 결과만 남긴다 (응답 본문은 버림). */
export function slim(r: Result & { body?: unknown; etag?: string | null }): Result {
  const { body: _body, etag: _etag, ...rest } = r;
  return rest;
}
