import { useEffect, useMemo, useRef, useState } from "react";
import type { Region } from "../api/types";
import { pushRecent, recentRegions, searchRegions, useRegions } from "../lib/regions";
import { usePopover } from "./ui";

/**
 * 시군구 선택: 버튼 → 팝오버.
 *  - 위: 검색(시군구·시도·코드, 자음만 입력하면 초성 검색 'ㅂㄷ' → 분당)
 *  - 아래: 시도 목록 | 선택한 시도의 시군구 격자  (256개를 한 줄 목록으로 늘어놓지 않음)
 *  - 최근 선택 6개, 키보드 ↑↓ Enter Esc
 */
export default function RegionPicker({ value, onChange, align = "left" }: { value: string; onChange: (code: string) => void; align?: "left" | "right" }) {
  const { regions, byCode, sidos } = useRegions();
  const { open, setOpen, ref } = usePopover();
  const current = byCode.get(value);
  const [q, setQ] = useState("");
  const [sido, setSido] = useState(current?.sidoCd ?? "11");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(-1);  // 격자에서는 방향키를 누르기 전까지 강조하지 않는다 (첫 칸이 선택된 것처럼 보이지 않게)
      if (current) setSido(current.sidoCd);
      setTimeout(() => input.current?.focus(), 0);
    }
  }, [open, current]);

  const results = useMemo(() => searchRegions(regions, q, 40), [q, regions]);
  const list: Region[] = q.trim() ? results : sidos.find((s) => s.sidoCd === sido)?.regions ?? [];
  const recent = recentRegions().map((c) => byCode.get(c)).filter((r): r is Region => !!r);

  const choose = (r: Region) => {
    pushRecent(r.sggCd);
    onChange(r.sggCd);
    setOpen(false);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, list.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && active >= 0 && list[active]) { e.preventDefault(); choose(list[active]); }
  };

  return (
    <div className="pop-anchor" ref={ref}>
      <button type="button" className="btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}
              style={{ minWidth: 200, justifyContent: "space-between" }}>
        <span>
          {current ? <><span className="muted">{current.sidoName} · </span>{current.name}</> : "시군구 선택"}
        </span>
        <span className="caret">▼</span>
      </button>
      {open && (
        <div className={`popover ${align === "right" ? "right" : ""}`} role="dialog" aria-label="시군구 선택">
          <div className="rp">
            <div className="rp-search">
              <input ref={input} value={q} placeholder="시군구·시도 이름, 코드 또는 초성(예: ㅂㄷ) 검색" aria-label="시군구 검색"
                     onChange={(e) => { setQ(e.target.value); setActive(0); }} onKeyDown={onKey} />
            </div>
            {!q.trim() && recent.length > 0 && (
              <div className="rp-recent">
                <span className="muted small">최근</span>
                {recent.map((r) => (
                  <button key={r.sggCd} type="button" className="chip" onClick={() => choose(r)}>{r.name}</button>
                ))}
              </div>
            )}
            {q.trim() ? (
              <div className="rp-results" role="listbox" aria-label="검색 결과">
                {results.length === 0 ? <div className="empty">일치하는 시군구가 없습니다.</div> : results.map((r, i) => (
                  <button key={r.sggCd} type="button" role="option" aria-selected={i === active} data-active={i === active}
                          onMouseEnter={() => setActive(i)} onClick={() => choose(r)}>
                    <strong>{r.name}</strong><span className="muted small">{r.sidoName} · {r.sggCd}</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="rp-body">
                <div className="rp-sido" role="listbox" aria-label="시도">
                  {sidos.map((s) => (
                    <button key={s.sidoCd} type="button" aria-pressed={s.sidoCd === sido} onClick={() => { setSido(s.sidoCd); setActive(-1); }}>
                      <span>{s.name}</span><span className="muted small">{s.regions.length}</span>
                    </button>
                  ))}
                </div>
                <div className="rp-sgg" role="listbox" aria-label="시군구">
                  {list.map((r, i) => (
                    <button key={r.sggCd} type="button" role="option" aria-selected={r.sggCd === value}
                            aria-current={r.sggCd === value} data-active={i === active} onClick={() => choose(r)}>
                      {r.name}<span className="code">{r.sggCd}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
