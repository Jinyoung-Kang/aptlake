import { useEffect, useRef, useState, type ReactNode } from "react";
import { DASH, num, pct } from "../lib/format";

/** 변화율: 부호·화살표·색을 함께 (색만으로 구분하지 않음). 상승 빨강 ▲ / 하락 파랑 ▼ */
export function Change({ v, digits = 1, suffix, title }: { v: number | null | undefined; digits?: number; suffix?: string; title?: string }) {
  if (v == null || Number.isNaN(v)) return <span className="chg na" title={title}>{DASH}<span className="sr-only">자료 없음</span></span>;
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const arrow = v > 0 ? "▲" : v < 0 ? "▼" : "";
  return (
    <span className={`chg ${cls}`} title={title}>
      <span aria-hidden="true">{arrow} {pct(v, digits)}{suffix ?? ""}</span>
      <span className="sr-only">{`${v > 0 ? "상승" : v < 0 ? "하락" : "변화 없음"} ${Math.abs(v).toFixed(digits)}%${suffix ?? ""}`}</span>
    </span>
  );
}

export function Badge({ children, tone, title }: { children: ReactNode; tone?: "warn" | "bad" | "good" | "info"; title?: string }) {
  return <span className={`badge ${tone ?? ""}`} title={title}>{children}</span>;
}

/** 제곱미터: 'm' 뒤 위첨자 2 를 글자 크기의 72%로 (호환 문자 ㎡·m² 글리프는 작아서 읽기 어렵다). */
export function Sqm() {
  return <span className="sqm">m<sup>2</sup></span>;
}

/** 문자열 속 '㎡'·'m²' 를 <Sqm/> 로 바꾼다 (API 가 준 단위·라벨에도 쓴다). 문자열이 아니면 그대로. */
export function sqm(text: ReactNode): ReactNode {
  if (typeof text !== "string" || !/㎡|m²/.test(text)) return text;
  // biome-ignore lint/suspicious/noArrayIndexKey: 글자를 나눈 위치가 곧 정체성이다 (순서가 바뀌지 않음)
  return text.split(/㎡|m²/).flatMap((part, i) => (i ? [<Sqm key={i} />, part] : [part]));
}

export function Kpi({ k, v, unit, d, title }: { k: ReactNode; v: ReactNode; unit?: string; d?: ReactNode; title?: string }) {
  return (
    <div className="kpi" title={title}>
      <div className="k">{sqm(k)}</div>
      <div className="v">{v}{unit ? <small>{sqm(unit)}</small> : null}</div>
      {d ? <div className="d">{d}</div> : null}
    </div>
  );
}

export function StatRow({ k, v }: { k: ReactNode; v: ReactNode }) {
  return <div className="stat-row"><span className="k">{sqm(k)}</span><span className="v">{sqm(v)}</span></div>;
}

/** 버튼 묶음 — 묶음 이름은 화면낭독기에만 (fieldset + 숨긴 legend). */
export function Group({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <fieldset className={`group ${className ?? ""}`}>
      <legend className="sr-only">{label}</legend>
      {children}
    </fieldset>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string; title?: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <Group label={label} className="seg">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} title={o.title} onClick={() => onChange(o.value)}>
          {sqm(o.label)}
        </button>
      ))}
    </Group>
  );
}

export function Tabs<T extends string>({ value, options, onChange }: {
  value: T; options: { value: T; label: string; n?: number }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {options.map((o) => (
        <button key={o.value} role="tab" type="button" aria-selected={o.value === value} onClick={() => onChange(o.value)}>
          {sqm(o.label)}{o.n != null ? <span className="n">{num(o.n)}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" aria-checked={checked} checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function ErrorBox({ error, onRetry }: { error: string | null | undefined; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      <span>{error}</span>
      {onRetry ? <button type="button" className="btn" onClick={onRetry}>다시 시도</button> : null}
    </div>
  );
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
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const inside = !!ref.current?.contains(document.activeElement);
      setOpen(false);
      // 팝업 안에 있던 초점이 닫히며 사라지지 않게 연 버튼으로 되돌린다 (WAI-ARIA 대화상자 패턴, QA-016)
      if (inside) ref.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return { open, setOpen, ref };
}
