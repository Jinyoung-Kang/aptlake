// QA 혼합 부하: 핵심 조회 경로를 경로별로 일정 도착률로 — 경로별 p95·실패율 (QA 스택 전용)
import http from "k6/http";
import { check } from "k6";
import { Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://api:8610";
const H = { headers: { "X-API-Key": __ENV.APTLAKE_KEY } };
const RATE = +(__ENV.RATE || 10);  // 경로마다 초당
const DURATION = __ENV.DURATION || "60s";
const ROUTES = ["distribution", "complexes", "overview", "search", "ticker", "geo", "complex", "index"];
const T = Object.fromEntries(ROUTES.map((r) => [r, new Trend(`${r}_ms`, true)]));

export const options = {
  scenarios: Object.fromEntries(ROUTES.map((r) => [r, { executor: "constant-arrival-rate", rate: r === "geo" ? 1 : RATE,
    timeUnit: "1s", duration: DURATION, preAllocatedVUs: 20, maxVUs: 100, exec: r }])),
};

export function setup() {
  const regions = http.get(`${BASE}/v1/regions`, H).json("items").map((x) => x.sggCd);
  const cx = http.get(`${BASE}/v1/regions/${regions[0]}/complexes?from=2025-01&to=2025-12&limit=50`, H).json("items").map((x) => x.complexKey);
  return { regions, cx };
}
const pick = (a) => a[Math.floor(Math.random() * a.length)];
const ym = () => `${2021 + Math.floor(Math.random() * 5)}-${String(1 + Math.floor(Math.random() * 12)).padStart(2, "0")}`;
function get(name, url) {
  const r = http.get(`${BASE}${url}`, Object.assign({ tags: { name } }, H));
  check(r, { [`${name} 200`]: (x) => x.status === 200 });
  T[name].add(r.timings.duration);
}
export function distribution(c) { get("distribution", `/v1/regions/${pick(c.regions)}/distribution?ym=${ym()}`); }
export function complexes(c) { const y = 2021 + Math.floor(Math.random() * 5); get("complexes", `/v1/regions/${pick(c.regions)}/complexes?from=${y}-01&to=${y}-12&limit=30`); }
export function overview() { get("overview", `/v1/market/overview?ym=${ym()}`); }
export function search() { get("search", `/v1/search?q=QA${Math.floor(Math.random() * 180)}`); }
export function ticker() { get("ticker", "/v1/market/ticker"); }
export function geo() { get("geo", "/v1/geo/sgg"); }
export function complex(c) { get("complex", `/v1/complexes/${pick(c.cx)}`); }
export function index() { get("index", `/v1/index?regionId=${pick(["00", "11", "26", "41", "48"])}`); }
