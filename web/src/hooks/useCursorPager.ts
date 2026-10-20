import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../api/client";

type Page<Row, Summary> = { items: Row[]; summary: Summary | null; page: { nextCursor: string | null } };

/**
 * 커서 페이지 목록 (거래·전월세 공용): 조건(첫 페이지 경로)이 바뀌면 첫 페이지(+조건 전체 요약)를 다시 읽고,
 * '더 보기'는 커서로 이어 붙인다. 늦게 온 '더 보기' 응답은 조건이 바뀌었으면 버린다 (다른 조건의 행이 섞이지 않게).
 * 조건이 바뀌면 선택도 푼다.
 */
export function useCursorPager<Row, Summary>(first: string | null, withCursor: (cursor: string) => string) {
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Row | null>(null);
  const current = useRef(first);
  current.current = first;

  useEffect(() => {
    if (!first) return;
    const ctrl = new AbortController();
    // 이전 조건의 커서는 바로 버린다 — 남아 있으면 새 조건을 읽는 동안 '더 보기'가 다른 조건의 다음 페이지를 부른다
    setLoading(true); setError(null); setSelected(null); setCursor(null);
    api<Page<Row, Summary>>(first, { signal: ctrl.signal })
      .then((p) => { setRows(p.items); setSummary(p.summary); setCursor(p.page.nextCursor); })
      .catch((e) => { if (!ctrl.signal.aborted) { setRows([]); setError(errorText(e)); } })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });  // 취소된 요청은 새 요청의 '불러오는 중'을 끄지 않는다
    return () => ctrl.abort();
  }, [first]);

  const more = () => {
    if (!first || !cursor) return;
    const asked = first;
    setLoading(true);
    api<Page<Row, Summary>>(withCursor(cursor))
      .then((p) => { if (current.current !== asked) return; setRows((r) => [...r, ...p.items]); setCursor(p.page.nextCursor); })
      .catch((e) => { if (current.current === asked) setError(errorText(e)); })
      .finally(() => { if (current.current === asked) setLoading(false); });
  };
  return { rows, summary, cursor, error, loading, more, selected, setSelected };
}
