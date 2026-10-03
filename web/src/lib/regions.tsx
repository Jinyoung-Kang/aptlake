import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { Region } from "../api/types";
import { useApi } from "../hooks/useApi";
import { initials } from "./format";
import { paths } from "../api/endpoints";

export type Sido = { sidoCd: string; name: string; regions: Region[] };
type Ctx = { regions: Region[]; byCode: Map<string, Region>; sidos: Sido[]; sidoName: (cd: string) => string; error: string | null };

const RegionsContext = createContext<Ctx>({ regions: [], byCode: new Map(), sidos: [], sidoName: (c) => c, error: null });

/** 시군구 목록(행정안전부 법정동코드 기반)은 앱 전체에서 한 번만 불러 공유한다. */
export function RegionsProvider({ children }: { children: ReactNode }) {
  const { data, error } = useApi<{ items: Region[] }>(paths.regions());
  const value = useMemo<Ctx>(() => {
    const regions = data?.items ?? [];
    const byCode = new Map(regions.map((r) => [r.sggCd, r]));
    const m = new Map<string, Sido>();
    for (const r of regions) {
      if (!m.has(r.sidoCd)) m.set(r.sidoCd, { sidoCd: r.sidoCd, name: r.sidoName, regions: [] });
      m.get(r.sidoCd)!.regions.push(r);
    }
    const sidos = [...m.values()].sort((a, b) => a.sidoCd.localeCompare(b.sidoCd));
    const names = new Map(sidos.map((s) => [s.sidoCd, s.name]));
    return { regions, byCode, sidos, sidoName: (cd: string) => (cd === "00" ? "전국" : names.get(cd) ?? cd), error };
  }, [data, error]);
  return <RegionsContext.Provider value={value}>{children}</RegionsContext.Provider>;
}

export function useRegions() {
  return useContext(RegionsContext);
}

const RECENT_KEY = "aptlake.recentRegions";
export function recentRegions(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string" && /^\d{5}$/.test(x)).slice(0, 6) : [];
  } catch {
    return [];
  }
}
export function pushRecent(code: string) {
  try {
    const next = [code, ...recentRegions().filter((c) => c !== code)].slice(0, 6);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    /* 저장소를 못 쓰는 환경(사생활 보호 모드 등)에서는 최근 목록만 생략 */
  }
}

/**
 * 시군구 검색 순위: 이름 앞부분 일치 > 이름 속 단어 앞부분('분당' → 성남시 분당구) > 이름 포함 > '시도 시군구' 포함('경기 성남').
 * 자음만 입력하면 시군구 이름의 초성으로만 비교한다 — 시도까지 초성 비교하면 'ㅂㄷ' 이 충청북도의 모든 시군구와 맞는다.
 * 숫자는 시군구 코드 앞부분.
 */
export function searchRegions(regions: Region[], query: string, limit = 40): Region[] {
  const q = query.replace(/\s+/g, "").toLowerCase();
  if (!q) return [];
  if (/^\d+$/.test(q)) return regions.filter((r) => r.sggCd.startsWith(q)).slice(0, limit);
  const cho = /^[ㄱ-ㅎ]+$/.test(q);
  const tokens = query.trim().toLowerCase().split(/\s+/);
  const hits: [number, Region][] = [];
  for (const r of regions) {
    const words = r.name.toLowerCase().split(/\s+/).map((w) => (cho ? initials(w) : w));
    const name = words.join("");
    let score = name.startsWith(q) ? 0 : words.some((w) => w.startsWith(q)) ? 1 : name.includes(q) ? 2 : -1;
    if (score < 0 && !cho && tokens.every((t) => r.fullName.toLowerCase().includes(t))) score = 3;
    if (score >= 0) hits.push([score, r]);
  }
  hits.sort((a, b) => a[0] - b[0] || a[1].sggCd.localeCompare(b[1].sggCd));
  return hits.slice(0, limit).map((h) => h[1]);
}
