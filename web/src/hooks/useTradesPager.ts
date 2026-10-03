import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../api/client";
import { paths, type TradeQuery } from "../api/endpoints";
import type { Trade, TradePage, TradeSummary } from "../api/types";

/**
 * 거래 목록: 조건이 바뀌면 첫 페이지(+조건 전체 요약)를 다시 읽고, '더 보기'는 커서로 이어 붙인다.
 * 늦게 온 '더 보기' 응답은 조건이 바뀌었으면 버린다 (다른 조건의 행이 섞이지 않게). 조건이 바뀌면 선택도 푼다.
 */
export function useTradesPager(query: TradeQuery | null) {
  const q = query ? paths.trades(query) : null;
  const [rows, setRows] = useState<Trade[]>([]);
  const [summary, setSummary] = useState<TradeSummary | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Trade | null>(null);
  const current = useRef(q);
  current.current = q;

  useEffect(() => {
    if (!q) return;
    const ctrl = new AbortController();
    setLoading(true); setError(null); setSelected(null);
    api<TradePage>(q, { signal: ctrl.signal })
      .then((p) => { setRows(p.items); setSummary(p.summary); setCursor(p.page.nextCursor); })
      .catch((e) => { if (!ctrl.signal.aborted) { setRows([]); setCursor(null); setError(errorText(e)); } })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [q]);

  const more = () => {
    if (!q || !query || !cursor) return;
    const asked = q;
    setLoading(true);
    api<TradePage>(paths.trades({ ...query, cursor }))
      .then((p) => { if (current.current !== asked) return; setRows((r) => [...r, ...p.items]); setCursor(p.page.nextCursor); })
      .catch((e) => { if (current.current === asked) setError(errorText(e)); })
      .finally(() => { if (current.current === asked) setLoading(false); });
  };
  return { rows, summary, cursor, error, loading, more, selected, setSelected };
}
