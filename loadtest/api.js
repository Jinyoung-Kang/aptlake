// k6 부하 시나리오 (기획서 10장): months 200 RPS · trades 100 RPS · 한도 초과(익명) — 로컬 맥 기준
import http from "k6/http";
import { check } from "k6";
import { Rate, Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://127.0.0.1:8610";
const KEY = __ENV.APTLAKE_KEY;
const H = { headers: { "X-API-Key": KEY } };
const cacheHit = new Rate("cache_hit");
const monthsMs = new Trend("months_ms", true);
const tradesMs = new Trend("trades_ms", true);
const hitMs = new Trend("cache_hit_ms", true);
const missMs = new Trend("cache_miss_ms", true);
const limited = new Rate("anon_429");
// 전후 비교(A/B)용: 기본은 기획서 목표 부하(200 + 100 RPS), 다른 대상·부하는 환경 변수로
const RATE_MONTHS = +(__ENV.RATE_MONTHS || 200);
const RATE_TRADES = +(__ENV.RATE_TRADES || 100);
const DURATION = __ENV.DURATION || "60s";

export const options = {
  scenarios: {
    months: { executor: "constant-arrival-rate", rate: RATE_MONTHS, timeUnit: "1s", duration: DURATION,
              preAllocatedVUs: 60, maxVUs: 200, exec: "months" },
    trades: { executor: "constant-arrival-rate", rate: RATE_TRADES, timeUnit: "1s", duration: DURATION,
              preAllocatedVUs: 40, maxVUs: 150, exec: "trades" },
    over_limit: { executor: "constant-arrival-rate", rate: 1, timeUnit: "1s", duration: DURATION,
                  preAllocatedVUs: 2, exec: "anonymous" },
  },
  thresholds: {
    "months_ms": ["p(95)<80"],
    "trades_ms": ["p(95)<150"],
    "http_req_failed{scenario:months}": ["rate<0.001"],
    "http_req_failed{scenario:trades}": ["rate<0.001"],
  },
};

export function setup() {
  const regions = http.get(`${BASE}/v1/regions`, H).json("items").map((r) => r.sggCd);
  const q = http.get(`${BASE}/v1/quality/summary`, H).json();
  return { regions, months: __ENV.MONTHS ? __ENV.MONTHS.split(",") : null, dataset: q.dataset };
}

function record(res) {
  const hit = res.headers["X-Cache"] === "hit";
  cacheHit.add(hit);
  (hit ? hitMs : missMs).add(res.timings.duration);
}

function pick(a) { return a[Math.floor(Math.random() * a.length)]; }
function ymAdd(ym, n) { const d = new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`; }

export function months(ctx) {
  const to = ymAdd("2026-09", -Math.floor(Math.random() * 24));
  const res = http.get(`${BASE}/v1/regions/${pick(ctx.regions)}/months?from=${ymAdd(to, -11)}&to=${to}`,
                       Object.assign({ tags: { name: "months" } }, H));
  check(res, { "months 200": (r) => r.status === 200 });
  monthsMs.add(res.timings.duration);
  record(res);
}

export function trades(ctx) {
  const m = ymAdd("2026-09", -Math.floor(Math.random() * 12));
  const last = new Date(+m.slice(0, 4), +m.slice(5, 7), 0).getDate();
  const res = http.get(`${BASE}/v1/trades?sggCd=${pick(ctx.regions)}&from=${m}-01&to=${m}-${last}&limit=200`,
                       Object.assign({ tags: { name: "trades" } }, H));
  check(res, { "trades 200": (r) => r.status === 200 });
  tradesMs.add(res.timings.duration);
  record(res);
}

// 익명 플랜(분당 20회)에 초당 1회 → 약 2/3 가 429 여야 한다
export function anonymous(ctx) {
  const res = http.get(`${BASE}/v1/regions/${pick(ctx.regions)}/months?from=2025-01&to=2025-06`, { tags: { name: "anon" } });
  check(res, { "anon 200 or 429": (r) => r.status === 200 || r.status === 429,
               "429 has Retry-After": (r) => r.status !== 429 || !!r.headers["Retry-After"] });
  limited.add(res.status === 429);
}
