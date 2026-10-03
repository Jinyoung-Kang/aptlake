import { useEffect, useState } from "react";

/** 현재 시각(ms)을 주기마다 갱신 — '경과·대기 시간'처럼 시간이 흐르며 바뀌는 표시용. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
