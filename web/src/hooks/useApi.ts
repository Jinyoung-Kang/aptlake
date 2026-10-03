import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { Available, Ticker } from "../api/types";
import { paths } from "../api/endpoints";

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

/** 조회 가능한 계약월 범위(발행된 데이터 기준) — 시세 띠 응답을 같이 쓴다 (클라이언트 캐시 60초). */
export function useAvailable(): Available | null {
  const { data } = useApi<Ticker>(paths.ticker());
  return data?.available ?? null;
}
