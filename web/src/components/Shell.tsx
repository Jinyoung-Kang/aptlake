import { type ReactNode, useCallback, useMemo, useRef, useState } from "react";
import type { TickerItem } from "../api/types";
import { useClickOutside } from "../hooks/useClickOutside";
import { useComplexSearch } from "../hooks/useComplexSearch";
import { useTheme } from "../hooks/useTheme";
import { useApi } from "../hooks/useApi";
import { kst, num } from "../lib/format";
import { href, navigate } from "../lib/router";
import { pushRecent, searchRegions, useRegions } from "../lib/regions";
import { Change, sqm } from "./ui";
import { paths } from "../api/endpoints";

export const NAV = [
  { path: "/market", label: "시장 개요" },
  { path: "/region", label: "지역 분석" },
  { path: "/trades", label: "거래 목록" },
  { path: "/index", label: "가격지수" },
  { path: "/quality", label: "데이터 품질" },
  { path: "/dev", label: "개발자" },
  { path: "/ops", label: "수집 상태" },
];

function SearchBox() {
  const { regions, byCode } = useRegions();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const complexes = useComplexSearch(q);
  const box = useRef<HTMLElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useClickOutside(box, close);

  const regionHits = useMemo(() => searchRegions(regions, q, 6), [q, regions]);

  const items: { key: string; go: () => void; main: string; sub: string; group: string }[] = [
    ...regionHits.map((r) => ({ key: `r${r.sggCd}`, group: "시군구", main: r.name, sub: `${r.sidoName} · ${r.sggCd}`,
      go: () => { pushRecent(r.sggCd); navigate(`/region/${r.sggCd}`); } })),
    ...complexes.map((c) => ({ key: `c${c.complexKey}`, group: "단지", main: c.aptName,
      sub: `${byCode.get(c.sggCd)?.fullName ?? c.sggCd} ${c.umdName}${c.buildYear ? ` · ${c.buildYear}년` : ""} · 거래 ${num(c.validTrades)}건`,
      go: () => navigate(`/complex/${c.complexKey}`) })),
  ];
  const pick = (i: number) => { items[i]?.go(); setOpen(false); setQ(""); };

  return (
    // biome-ignore lint/a11y/noRedundantRoles: <search> 를 모르는 브라우저(사파리 17·크롬 118 이전)에서도 검색 영역으로 알리게
    <search className="search" ref={box} role="search">
      <span className="glass" aria-hidden="true">⌕</span>
      <input value={q} placeholder="시군구·단지명 검색 (예: 분당, ㅂㄷ, 래미안)" aria-label="시군구·단지 검색"
             role="combobox" aria-expanded={open && items.length > 0} aria-controls="search-results"
             onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setActive(0); setOpen(true); }}
             onKeyDown={(e) => {
               if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, items.length - 1)); }
               else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
               else if (e.key === "Enter") { e.preventDefault(); pick(active); }
               else if (e.key === "Escape") setOpen(false);
             }} />
      {open && q.trim() && (
        <div className="search-results" id="search-results" role="listbox">
          {items.length === 0 ? <div className="empty">검색 결과가 없습니다.</div> : items.map((it, i) => (
            <div key={it.key}>
              {(i === 0 || items[i - 1].group !== it.group) && <div className="group">{it.group}</div>}
              <button type="button" role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onClick={() => pick(i)}>
                <strong>{it.main}</strong><span className="sub">{it.sub}</span>
              </button>
            </div>
          ))}
        </div>
      )}
    </search>
  );
}

function Ticker() {
  const { data } = useApi<{ month: string; items: TickerItem[] }>(paths.ticker());
  if (!data?.items.length) return null;
  return (
    <section className="ticker" aria-label="주요 지표">
      <div className="ticker-inner">
        {data.items.map((t) => (
          <div className="tick" key={t.key} title={t.changeBasis ?? undefined}>
            <span className="l">{sqm(t.label.replace(/(\d{4})-(\d{2})/, "$1.$2"))}</span>
            <span className="v">{t.value == null ? "–" : num(t.value, t.unit === "%" ? 1 : t.key.startsWith("index") ? 1 : 0)}{t.unit && t.unit !== "건" ? sqm(t.unit) : ""}</span>
            {t.change != null && <Change v={t.change} digits={t.key.startsWith("index") ? 2 : 1} />}
            {t.provisional ? <span className="muted small">잠정</span> : null}
          </div>
        ))}
      </div>
    </section>
  );
}

export function Shell({ path, meta, children }: { path: string; meta: { version?: string; asOf?: string | null }; children: ReactNode }) {
  const theme = useTheme();
  const { error } = useRegions();
  return (
    <>
      <header className="header">
        <div className="topbar">
          <a className="brand" href={href("/market")} aria-label="AptLake 홈"><span className="mark" aria-hidden="true" />AptLake<span className="sub">아파트 실거래 데이터</span></a>
          <SearchBox />
          <div className="right">
            <span className="meta-chip" title="데이터셋 버전 · 원천 관측 시각">{meta.version ?? "–"} · 원천 {kst(meta.asOf ?? null, false)}</span>
            <button type="button" className="icon-btn theme-btn" aria-label={theme.label} title={theme.label} onClick={theme.cycle}>
              <span aria-hidden="true">{theme.choice === "system" ? "◐" : theme.choice === "dark" ? "☾" : "☀"}</span>
              <span className="theme-name">{theme.choice === "system" ? "자동" : theme.choice === "dark" ? "어둡게" : "밝게"}</span>
            </button>
          </div>
        </div>
        <nav className="nav" aria-label="메뉴">
          <div className="nav-inner">
            {NAV.map((n) => (
              <a key={n.path} href={href(n.path)} aria-current={path.startsWith(n.path) ? "page" : undefined}>{n.label}</a>
            ))}
          </div>
        </nav>
        <Ticker />
      </header>
      <main className="container">
        {error ? <p className="error">시군구 목록을 불러오지 못했습니다 — {error}</p> : null}
        {children}
      </main>
      <footer className="footer">
        <div className="footer-inner">
          원천: 국토교통부 아파트 매매 실거래가 자료 · 행정안전부 법정동코드 · 한국부동산원 R-ONE 부동산통계 · 국토정보플랫폼 V-World 행정경계.
          공개 신고 자료를 가공한 학습·포트폴리오용 서비스이며 공식 통계가 아닙니다. 투자 판단의 근거로 쓰지 마세요.
          <br />API 문서 <a href="/docs" target="_blank" rel="noreferrer">/docs</a> · 데이터셋 {meta.version ?? "–"}
        </div>
      </footer>
    </>
  );
}
