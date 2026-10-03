import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiSend, errorText, fetchRetry } from "../api/client";

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("일시 오류 재시도", () => {
  it("502·503·504 는 최대 2번 다시 시도한다", async () => {
    fetchMock.mockResolvedValueOnce(json(503, {})).mockResolvedValueOnce(json(502, {})).mockResolvedValueOnce(json(200, { ok: 1 }));
    const p = fetchRetry("/v1/x", {});
    await vi.runAllTimersAsync();
    expect((await p).status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
  it("429·500 은 다시 시도하지 않는다", async () => {
    fetchMock.mockResolvedValueOnce(json(429, {}));
    expect((await fetchRetry("/v1/x", {})).status).toBe(429);
    fetchMock.mockResolvedValueOnce(json(500, {}));
    expect((await fetchRetry("/v1/x", {})).status).toBe(500);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  it("세 번 모두 실패하면 마지막 응답을 돌려준다", async () => {
    fetchMock.mockResolvedValue(json(503, {}, { "retry-after": "1" }));
    const p = fetchRetry("/v1/x", {});
    await vi.runAllTimersAsync();
    expect((await p).status).toBe(503);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("조회 캐시와 오류", () => {
  it("같은 경로는 60초 동안 다시 요청하지 않고, fresh 면 다시 요청한다", async () => {
    fetchMock.mockImplementation(async () => json(200, { n: fetchMock.mock.calls.length }));
    expect(await api("/v1/cache-test")).toEqual({ n: 1 });
    expect(await api("/v1/cache-test")).toEqual({ n: 1 });
    expect(await api("/v1/cache-test", { fresh: true })).toEqual({ n: 2 });
    vi.advanceTimersByTime(61_000);
    expect(await api("/v1/cache-test")).toEqual({ n: 3 });
  });
  it("Problem Details 는 ApiError 로, 화면 문구는 상태별로", async () => {
    fetchMock.mockResolvedValueOnce(json(429, { status: 429, code: "RATE_LIMITED", title: "Too Many Requests" }));
    const e = await api("/v1/err-429").catch((x) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect(errorText(e)).toBe("요청 한도를 넘었습니다 (RATE_LIMITED). 잠시 후 다시 시도하세요.");
    expect(errorText(new ApiError({ status: 500, code: "INTERNAL", title: "x", traceId: "0123456789abcdef" })))
      .toBe("서버 내부 오류로 불러오지 못했습니다 (INTERNAL · 추적 ID 0123456789ab). 수집 상태 메뉴의 오류 로그에서 추적 ID 로 찾을 수 있습니다.");
    expect(errorText(new ApiError({ status: 400, code: "INVALID_RANGE", title: "Invalid Range", detail: "to 는 from 이후" })))
      .toBe("Invalid Range — to 는 from 이후 (INVALID_RANGE)");
    expect(errorText(new DOMException("x", "AbortError"))).toBe("");
  });
});

describe("QA-012 같은 조회를 동시에 부르면 요청은 한 번", () => {
  it("응답이 오기 전 같은 경로를 두 번 부르면 fetch 는 한 번이고 둘 다 같은 결과를 받는다", async () => {
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => { resolve = r; }));
    const a = api<{ v: number }>("/v1/qa012/same");
    const b = api<{ v: number }>("/v1/qa012/same");
    resolve(json(200, { v: 1 }));
    expect(await a).toEqual({ v: 1 });
    expect(await b).toEqual({ v: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
  it("한 호출자가 취소해도 같은 요청을 기다리는 다른 호출자는 결과를 받는다", async () => {
    let resolve!: (r: Response) => void;
    fetchMock.mockReturnValueOnce(new Promise<Response>((r) => { resolve = r; }));
    const ctrl = new AbortController();
    const a = api("/v1/qa012/abort", { signal: ctrl.signal });
    const b = api<{ v: number }>("/v1/qa012/abort");
    ctrl.abort();
    resolve(json(200, { v: 2 }));
    await expect(a).rejects.toThrow();
    expect(await b).toEqual({ v: 2 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("QA-001 쓰기 요청은 사용자가 넣은 운영자 키를 그 요청에만 붙인다", () => {
  it("apiSend 에 키를 주면 X-API-Key 로 보낸다", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { cleared: null }));
    await apiSend("DELETE", "/v1/ops/errors/clear", "al_live_TESTTESTTEST.secret");
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("al_live_TESTTESTTEST.secret");
  });
});
