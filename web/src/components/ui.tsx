import { useEffect, useRef, useState, type ReactNode } from "react";
import { DASH, num, pct } from "../lib/format";

/** 변화율: 부호·화살표·색을 함께 (색만으로 구분하지 않음). 상승 빨강 ▲ / 하락 파랑 ▼ */
export function Change({ v, digits = 1, suffix, title }: { v: number | null | undefined; digits?: number; suffix?: string; title?: string }) {
  if (v == null || Number.isNaN(v)) return <span className="chg na" title={title}>{DASH}</span>;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const arrow = v > 0 ? "▲" : v < 0 ? "▼" : "";
  return (
    <span className={`chg ${cls}`} title={title} aria-label={`${v > 0 ? "상승" : v < 0 ? "하락" : "변화 없음"} ${Math.abs(v).toFixed(digits)}%`}>
      {arrow} {pct(v, digits)}{suffix ?? ""}
    </span>
  );
}

export function Badge({ children, tone, title }: { children: ReactNode; tone?: "warn" | "bad" | "good" | "info"; title?: string }) {
  return <span className={`badge ${tone ?? ""}`} title={title}>{children}</span>;
}

export function Kpi({ k, v, unit, d, title }: { k: ReactNode; v: ReactNode; unit?: string; d?: ReactNode; title?: string }) {
  return (
    <div className="kpi" title={title}>
      <div className="k">{k}</div>
      <div className="v">{v}{unit ? <small>{unit}</small> : null}</div>
      {d ? <div className="d">{d}</div> : null}
    </div>
  );
}

export function StatRow({ k, v }: { k: ReactNode; v: ReactNode }) {
  return <div className="stat-row"><span className="k">{k}</span><span className="v">{v}</span></div>;
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} title={o.title} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: string; n?: number }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" type="button" aria-selected={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}{o.n != null ? <span className="n">{num(o.n)}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return <p className="error" role="alert">{error}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Skeleton({ h = 16, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} aria-hidden="true" />;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // 클립보드 권한이 없을 때(비보안 문맥 등) textarea 선택 복사로 대체
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  }
}

export function CopyButton({ text, label = "복사", className = "btn", disabled }: { text: string | (() => string); label?: string; className?: string; disabled?: boolean }) {
  const [done, setDone] = useState<null | boolean>(null);
  useEffect(() => {
    if (done === null) return;
    const t = setTimeout(() => setDone(null), 1600);
    return () => clearTimeout(t);
  }, [done]);
  return (
    <button type="button" className={className} disabled={disabled} onClick={async (e) => {
      e.stopPropagation();
      setDone(await copyText(typeof text === "function" ? text() : text));
    }}>
      {done === true ? "✓ 복사됨" : done === false ? "복사 실패" : label}
    </button>
  );
}

/** 작은 추이선 (SVG). 마지막 점 강조. 값이 없으면 끊어 그린다. */
export function Sparkline({ values, w = 96, h = 26, color = "var(--series-1)", label }: {
  values: (number | null)[]; w?: number; h?: number; color?: string; label?: string;
}) {
  const vs = values.filter((v): v is number => v != null);
  if (vs.length < 2) return <span className="muted small">{DASH}</span>;
  const min = Math.min(...vs), max = Math.max(...vs);
  const span = max - min || 1;
  const step = w / (values.length - 1);
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) { pen = false; return; }
    const x = i * step, y = h - 3 - ((v - min) / span) * (h - 6);
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  });
  const lastI = values.map((v, i) => (v == null ? -1 : i)).filter((i) => i >= 0).pop() ?? 0;
  const lx = lastI * step, ly = h - 3 - (((values[lastI] as number) - min) / span) * (h - 6);
  return (
    <svg width={w} height={h} role="img" aria-label={label ?? "추이"} style={{ display: "block" }}>
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r={2.5} fill={color} />
    </svg>
  );
}

/** 범위 막대: [lo, hi] 안에서 v 의 위치. */
export function RangeBar({ lo, hi, v }: { lo: number; hi: number; v: number }) {
  const span = hi - lo || 1;
  const left = ((v - lo) / span) * 100;
  return (
    <div className="range-bar" aria-hidden="true">
      <div className="fill" style={{ left: 0, width: "100%", opacity: 0.25 }} />
      <div className="dot" style={{ left: `calc(${Math.min(100, Math.max(0, left))}% - 1px)` }} />
    </div>
  );
}

/** 바깥 클릭·Esc 로 닫는 팝오버 상태. */
export function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return { open, setOpen, ref };
}
