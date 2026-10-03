import { useEffect, useState } from "react";
import { api } from "../api/client";
import { paths } from "../api/endpoints";
import type { ComplexHit } from "../api/types";
import { complexSearchTerm } from "../domain/search";

const DEBOUNCE_MS = 220;
const MAX_HITS = 8;

/** 단지 이름 검색 — 입력이 멈춘 뒤 한 번만 보내고, 새 입력이 오면 이전 요청은 취소. 실패는 조용히 무시(시군구 결과는 그대로). */
export function useComplexSearch(q: string): ComplexHit[] {
  const [hits, setHits] = useState<ComplexHit[]>([]);
  useEffect(() => {
    const term = complexSearchTerm(q);
    if (!term) { setHits([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      api<{ complexes: ComplexHit[] }>(paths.search(term), { signal: ctrl.signal })
        .then((r) => setHits(r.complexes.slice(0, MAX_HITS))).catch(() => undefined);
    }, DEBOUNCE_MS);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q]);
  return hits;
}
