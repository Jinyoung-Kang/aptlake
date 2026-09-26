// 숫자·날짜 서식과 한글 초성 검색 — 표시 규칙을 한곳에 모은다.
const nf0 = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const DASH = "–";

export function num(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return DASH;
  return (digits === 0 ? nf0 : digits === 1 ? nf1 : nf2).format(v);
}

/** 만원 단위 금액 → '19억 5,200만' (표·툴팁용). */
export function manwon(v: number | null | undefined): string {
  if (v == null) return DASH;
  const eok = Math.floor(v / 10000);
  const rest = Math.round(v % 10000);
  if (eok === 0) return `${nf0.format(rest)}만`;
  return rest === 0 ? `${eok}억` : `${eok}억 ${nf0.format(rest)}만`;
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null || Number.isNaN(v)) return DASH;
  const s = v.toFixed(digits);
  return `${v > 0 ? "+" : ""}${s}%`;
}

const KST = "Asia/Seoul";
const dtf = new Intl.DateTimeFormat("ko-KR", {
  timeZone: KST, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
});

function parts(d: Date): Record<string, string> {
  const o: Record<string, string> = {};
  for (const p of dtf.formatToParts(d)) o[p.type] = p.value;
  return o;
}

/** ISO → 'YYYY-MM-DD HH:mm:ss' (KST). */
export function kst(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return DASH;
  const p = parts(new Date(iso));
  const hh = p.hour === "24" ? "00" : p.hour;
  return `${p.year}-${p.month}-${p.day} ${hh}:${p.minute}${withSeconds ? `:${p.second}` : ""}`;
}

/** ISO → 'MM-DD HH:mm' (KST, 같은 해). */
export function kstShort(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const p = parts(new Date(iso));
  const hh = p.hour === "24" ? "00" : p.hour;
  return `${p.month}-${p.day} ${hh}:${p.minute}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return DASH;
  const s = Math.round(seconds);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60), r = s % 60;
  if (m < 60) return r ? `${m}분 ${r}초` : `${m}분`;
  const h = Math.floor(m / 60);
  return `${h}시간 ${m % 60}분`;
}

export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return DASH;
  const diff = (now - new Date(iso).getTime()) / 1000;
  if (diff < 0) {
    const f = -diff;
    if (f < 60) return "곧";
    if (f < 3600) return `${Math.round(f / 60)}분 후`;
    if (f < 86400) return `${Math.round(f / 3600)}시간 후`;
    return `${Math.round(f / 86400)}일 후`;
  }
  if (diff < 60) return "방금";
  if (diff < 3600) return `${Math.round(diff / 60)}분 전`;
  if (diff < 86400) return `${Math.round(diff / 3600)}시간 전`;
  return `${Math.round(diff / 86400)}일 전`;
}

// ───── 월 연산 ('YYYY-MM') ─────
export function ymOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
export function ymAdd(ym: string, n: number): string {
  const [y, m] = ym.split("-").map(Number);
  return ymOf(new Date(y, m - 1 + n, 1));
}
export function ymDiff(a: string, b: string): number {
  const [ya, ma] = a.split("-").map(Number);
  const [yb, mb] = b.split("-").map(Number);
  return (yb - ya) * 12 + (mb - ma);
}
export function ymLabel(ym: string | null | undefined): string {
  return ym ? ym.replace("-", ".") : DASH;
}
export function monthRange(a: string, b: string): string[] {
  const out: string[] = [];
  for (let m = a; m <= b; m = ymAdd(m, 1)) out.push(m);
  return out;
}
export function lastDay(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  return `${ym}-${String(new Date(y, m, 0).getDate()).padStart(2, "0")}`;
}
export function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ───── 한글 초성 검색 ─────
const CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
export function initials(s: string): string {
  let out = "";
  for (const ch of s) {
    const c = ch.charCodeAt(0) - 0xac00;
    out += c >= 0 && c < 11172 ? CHO[Math.floor(c / 588)] : ch;
  }
  return out;
}

/** 선형 보간 분위수 (서버·numpy 기본값과 같은 정의). */
export function quantile(values: number[], q: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const h = (s.length - 1) * q;
  const lo = Math.floor(h), hi = Math.ceil(h);
  return s[lo] + (h - lo) * (s[hi] - s[lo]);
}

/**
 * 차트 툴팁은 HTML 문자열로 그려진다 (ECharts 가 innerHTML 로 넣음).
 * API 가 준 이름(시군구·시도·단지)은 반드시 이 함수로 감싼다 — 원천 자료에 태그가 섞여 와도 실행되지 않게.
 */
export function esc(v: unknown): string {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

