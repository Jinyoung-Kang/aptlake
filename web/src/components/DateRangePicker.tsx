import { useEffect, useState } from "react";
import { isoDate } from "../lib/format";
import { usePopover } from "./ui";

const DOW = ["일", "월", "화", "수", "목", "금", "토"];
const fmt = (s: string) => s.replaceAll("-", ".");
const addDays = (s: string, n: number) => { const d = new Date(`${s}T00:00:00`); d.setDate(d.getDate() + n); return isoDate(d); };
const spanDays = (a: string, b: string) => Math.round((new Date(`${b}T00:00:00`).getTime() - new Date(`${a}T00:00:00`).getTime()) / 86400000) + 1;

/** 날짜 범위 달력 (클릭 두 번). */
export default function DateRangePicker({ from, to, min, max, maxDays, onChange }: {
  from: string; to: string; min: string; max: string; maxDays?: number; onChange: (from: string, to: string) => void;
}) {
  const { open, setOpen, ref } = usePopover();
  const [view, setView] = useState(to.slice(0, 7));
  const [start, setStart] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => { if (open) { setView(to.slice(0, 7)); setStart(null); setHover(null); } }, [open, to]);

  const [vy, vm] = view.split("-").map(Number);
  const first = new Date(vy, vm - 1, 1);
  const days: { date: string; other: boolean }[] = [];
  const lead = first.getDay();
  for (let i = -lead; i < 42 - lead; i++) {
    const d = new Date(vy, vm - 1, 1 + i);
    days.push({ date: isoDate(d), other: d.getMonth() !== vm - 1 });
  }
  const move = (n: number) => { const d = new Date(vy, vm - 1 + n, 1); setView(isoDate(d).slice(0, 7)); };
  const monthStart = (d: Date) => isoDate(new Date(d.getFullYear(), d.getMonth(), 1));
  const today = new Date(`${max}T00:00:00`);
  const presets: { label: string; range: () => [string, string] }[] = [
    { label: "최근 7일", range: () => [addDays(max, -6), max] },
    { label: "최근 30일", range: () => [addDays(max, -29), max] },
    { label: "이번 달", range: () => [monthStart(today), max] },
    { label: "지난 달", range: () => { const s = new Date(today.getFullYear(), today.getMonth() - 1, 1); return [isoDate(s), isoDate(new Date(today.getFullYear(), today.getMonth(), 0))]; } },
    { label: "최근 3개월", range: () => [monthStart(new Date(today.getFullYear(), today.getMonth() - 2, 1)), max] },
  ];
  const tooFar = (d: string) => !!(start && maxDays && Math.abs(spanDays(start, d)) > maxDays);
  const click = (d: string) => {
    if (!start) { setStart(d); return; }
    const [a, b] = start <= d ? [start, d] : [d, start];
    onChange(a, b);
    setOpen(false);
  };
  const [lo, hi] = start ? (hover && hover < start ? [hover, start] : [start, hover ?? start]) : [from, to];
  const canPrev = view > min.slice(0, 7), canNext = view < max.slice(0, 7);

  return (
    <div className="pop-anchor" ref={ref}>
      <button type="button" className="btn" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen(!open)}>
        📅 {fmt(from)} – {fmt(to)} <span className="caret">▼</span>
      </button>
      {open && (
        <div className="popover" role="dialog" aria-label="계약일 기간 선택">
          <div className="mp" style={{ width: 450 }}>
            <div className="mp-presets">
              {presets.map((p) => (
                <button key={p.label} type="button" onClick={() => { const [a, b] = p.range(); onChange(a < min ? min : a, b); setOpen(false); }}>{p.label}</button>
              ))}
            </div>
            <div className="mp-main cal">
              <div className="mp-year">
                <button type="button" aria-label="이전 달" disabled={!canPrev} onClick={() => move(-1)}>‹</button>
                <span>{vy}년 {vm}월</span>
                <button type="button" aria-label="다음 달" disabled={!canNext} onClick={() => move(1)}>›</button>
              </div>
              {/* biome-ignore lint/a11y/noStaticElementInteractions: 마우스가 벗어나면 범위 미리보기 강조만 지운다 (각 날짜는 버튼이라 키보드로 고를 수 있음) */}
              <div className="cal-grid" onMouseLeave={() => setHover(null)}>
                {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
                {days.map(({ date, other }) => {
                  const disabled = date < min || date > max || tooFar(date);
                  const edge = date === lo || date === hi;
                  const inRange = date > lo && date < hi;
                  return (
                    <button key={date} type="button" disabled={disabled} onMouseEnter={() => setHover(date)} onClick={() => click(date)}
                            className={`${edge ? "edge" : inRange ? "in" : ""} ${other ? "other" : ""}`} aria-label={date}>
                      {Number(date.slice(8))}
                    </button>
                  );
                })}
              </div>
              <div className="mp-hint">{start ? `끝 날짜를 누르세요 (시작 ${fmt(start)})` : "시작 날짜를 누르세요"}{maxDays ? ` · 최대 ${maxDays}일` : ""}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
