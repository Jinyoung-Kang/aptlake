import { useEffect, useState } from "react";
import { ymAdd, ymDiff, ymLabel } from "../lib/format";
import { usePopover } from "./ui";

const MONTHS = ["1월", "2월", "3월", "4월", "5월", "6월", "7월", "8월", "9월", "10월", "11월", "12월"];

function clamp(ym: string, min: string, max: string) {
  return ym < min ? min : ym > max ? max : ym;
}

/** 월 범위 선택 (클릭 두 번: 시작 → 끝). 키보드 입력 없이 마우스로. */
export function MonthRangePicker({ from, to, min, max, maxSpan, onChange }: {
  from: string; to: string; min: string; max: string; maxSpan?: number; onChange: (from: string, to: string) => void;
}) {
  const { open, setOpen, ref } = usePopover();
  const [year, setYear] = useState(Number(to.slice(0, 4)));
  const [start, setStart] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => { if (open) { setYear(Number(to.slice(0, 4))); setStart(null); setHover(null); } }, [open, to]);

  const minY = Number(min.slice(0, 4)), maxY = Number(max.slice(0, 4));
  const presets: { label: string; months: number | "all" | "ytd" }[] = [
    { label: "최근 6개월", months: 6 }, { label: "최근 1년", months: 12 }, { label: "최근 2년", months: 24 },
    { label: "최근 3년", months: 36 }, { label: "올해", months: "ytd" }, { label: "전체", months: "all" },
  ];
  const applyPreset = (m: number | "all" | "ytd") => {
    let a: string;
    if (m === "all") a = min;
    else if (m === "ytd") a = `${max.slice(0, 4)}-01`;
    else a = ymAdd(max, -(m - 1));
    a = clamp(a, min, max);
    if (maxSpan && ymDiff(a, max) + 1 > maxSpan) a = ymAdd(max, -(maxSpan - 1));
    onChange(a, max);
    setOpen(false);
  };
  const tooFar = (ym: string) => !!(start && maxSpan && Math.abs(ymDiff(start, ym)) + 1 > maxSpan);
  const click = (ym: string) => {
    if (!start) { setStart(ym); return; }
    const [a, b] = start <= ym ? [start, ym] : [ym, start];
    onChange(a, b);
    setOpen(false);
  };
  const [lo, hi] = start ? (hover && hover < start ? [hover, start] : [start, hover ?? start]) : [from, to];

  return (
    <div className="pop-anchor" ref={ref}>
      <button type="button" className="btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        📅 {ymLabel(from)} – {ymLabel(to)} <span className="caret">▼</span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="기간 선택">
          <div className="mp">
            <div className="mp-presets">
              {presets.map((p) => <button key={p.label} type="button" onClick={() => applyPreset(p.months)}>{p.label}</button>)}
            </div>
            <div className="mp-main">
              <div className="mp-year">
                <button type="button" aria-label="이전 해" disabled={year <= minY} onClick={() => setYear(year - 1)}>‹</button>
                <span>{year}년</span>
                <button type="button" aria-label="다음 해" disabled={year >= maxY} onClick={() => setYear(year + 1)}>›</button>
              </div>
              <div className="mp-grid" onMouseLeave={() => setHover(null)}>
                {MONTHS.map((label, i) => {
                  const ym = `${year}-${String(i + 1).padStart(2, "0")}`;
                  const disabled = ym < min || ym > max || tooFar(ym);
                  const edge = ym === lo || ym === hi;
                  const inRange = ym > lo && ym < hi;
                  return (
                    <button key={ym} type="button" disabled={disabled} className={edge ? "edge" : inRange ? "in" : ""}
                            onMouseEnter={() => setHover(ym)} onClick={() => click(ym)} aria-label={`${year}년 ${label}`}>
                      {label}
                    </button>
                  );
                })}
              </div>
              <div className="mp-hint">
                {start ? `끝 달을 누르세요 (시작 ${ymLabel(start)})` : "시작 달을 누르세요"}
                {maxSpan ? ` · 최대 ${maxSpan}개월` : ""}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 빠른 기간 버튼 (시세 차트의 1Y·3Y·Max 처럼). */
export function RangePills({ from, to, min, max, maxSpan, onChange }: {
  from: string; to: string; min: string; max: string; maxSpan?: number; onChange: (from: string, to: string) => void;
}) {
  const opts: { label: string; n: number | "all" }[] = [
    { label: "6개월", n: 6 }, { label: "1년", n: 12 }, { label: "2년", n: 24 }, { label: "3년", n: 36 }, { label: "전체", n: "all" },
  ];
  return (
    <div className="seg" role="group" aria-label="빠른 기간">
      {opts.map((o) => {
        let a = o.n === "all" ? min : clamp(ymAdd(max, -((o.n as number) - 1)), min, max);
        if (maxSpan && ymDiff(a, max) + 1 > maxSpan) a = ymAdd(max, -(maxSpan - 1));
        const on = from === a && to === max;
        return <button key={o.label} type="button" aria-pressed={on} onClick={() => onChange(a, max)}>{o.label}</button>;
      })}
    </div>
  );
}

/** 단일 월 선택: ◀ [2026.06 ▼] ▶ */
export function MonthPicker({ value, min, max, onChange, label = "기준월" }: {
  value: string; min: string; max: string; onChange: (ym: string) => void; label?: string;
}) {
  const { open, setOpen, ref } = usePopover();
  const [year, setYear] = useState(Number(value.slice(0, 4)));
  useEffect(() => { if (open) setYear(Number(value.slice(0, 4))); }, [open, value]);
  const minY = Number(min.slice(0, 4)), maxY = Number(max.slice(0, 4));
  return (
    <div className="pop-anchor" ref={ref} style={{ display: "inline-flex", gap: 4 }}>
      <button type="button" className="btn" aria-label="이전 달" disabled={value <= min} onClick={() => onChange(ymAdd(value, -1))}>◀</button>
      <button type="button" className="btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        {label} {ymLabel(value)} <span className="caret">▼</span>
      </button>
      <button type="button" className="btn" aria-label="다음 달" disabled={value >= max} onClick={() => onChange(ymAdd(value, 1))}>▶</button>
      {open && (
        <div className="popover" role="dialog" aria-label={`${label} 선택`}>
          <div className="mp single">
            <div className="mp-main">
              <div className="mp-year">
                <button type="button" aria-label="이전 해" disabled={year <= minY} onClick={() => setYear(year - 1)}>‹</button>
                <span>{year}년</span>
                <button type="button" aria-label="다음 해" disabled={year >= maxY} onClick={() => setYear(year + 1)}>›</button>
              </div>
              <div className="mp-grid">
                {MONTHS.map((l, i) => {
                  const ym = `${year}-${String(i + 1).padStart(2, "0")}`;
                  return (
                    <button key={ym} type="button" disabled={ym < min || ym > max} className={ym === value ? "edge" : ""}
                            onClick={() => { onChange(ym); setOpen(false); }}>{l}</button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
