import { paths, type TradeQuery } from "../api/endpoints";
import type { Trade, TradeSummary } from "../api/types";
import { useCursorPager } from "./useCursorPager";

/** 거래 목록 페이지 (규칙은 useCursorPager — 조건이 바뀌면 첫 페이지부터, 늦은 '더 보기'는 버림) */
export function useTradesPager(query: TradeQuery | null) {
  return useCursorPager<Trade, TradeSummary>(query ? paths.trades(query) : null, (cursor) => paths.trades({ ...(query as TradeQuery), cursor }));
}
