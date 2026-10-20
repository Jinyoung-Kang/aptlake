// 공개 API 경로 — 화면이 쓰는 모든 조회 경로를 한곳에서 만든다 (쿼리 이름·형식이 흩어지지 않게).
// 경로 문자열이 곧 클라이언트 캐시 키이므로 같은 조회는 늘 같은 문자열이 되어야 한다.

export type TradeQuery = {
  sgg: string; from: string; to: string; includeCancelled: boolean; limit: number;
  minArea?: number; maxArea?: number; cursor?: string;
};

export type RentQuery = {
  sgg: string; from: string; to: string; limit: number; kind?: "jeonse" | "wolse";
  minArea?: number; maxArea?: number; cursor?: string;
};
/** 품질·수집 원천 (ADR-050): rtms = 매매(기본), rent = 전월세 */
export type Source = "rtms" | "rent";
const src = (s: Source, sep: "?" | "&") => (s === "rtms" ? "" : `${sep}source=${s}`);

export const paths = {
  regions: () => "/v1/regions",
  ticker: () => "/v1/market/ticker",
  marketOverview: (ym: string | null) => `/v1/market/overview${ym ? `?ym=${ym}` : ""}`,
  geoSgg: () => "/v1/geo/sgg",
  search: (term: string) => `/v1/search?q=${encodeURIComponent(term)}`,
  regionMonths: (sgg: string, from: string, to: string) => `/v1/regions/${sgg}/months?from=${from}&to=${to}`,
  regionDistribution: (sgg: string, ym: string) => `/v1/regions/${sgg}/distribution?ym=${ym}`,
  regionComplexes: (sgg: string, from: string, to: string, limit = 50) => `/v1/regions/${sgg}/complexes?from=${from}&to=${to}&limit=${limit}`,
  trades: (q: TradeQuery) =>
    `/v1/trades?sggCd=${q.sgg}&from=${q.from}&to=${q.to}&includeCancelled=${q.includeCancelled}&limit=${q.limit}` +
    `${q.minArea != null ? `&minArea=${q.minArea}` : ""}${q.maxArea != null ? `&maxArea=${q.maxArea}` : ""}` +
    `${q.cursor ? `&cursor=${encodeURIComponent(q.cursor)}` : ""}`,
  tradeHistory: (tradeId: string) => `/v1/trades/${tradeId}/history`,
  rentMonths: (sgg: string, from: string, to: string) => `/v1/regions/${sgg}/rent/months?from=${from}&to=${to}`,
  jeonseRatio: (sgg: string, from: string, to: string) => `/v1/regions/${sgg}/jeonse-ratio?from=${from}&to=${to}`,
  rentDistribution: (sgg: string, ym: string) => `/v1/regions/${sgg}/rent/distribution?ym=${ym}`,
  rents: (q: RentQuery) =>
    `/v1/rents?sggCd=${q.sgg}&from=${q.from}&to=${q.to}&limit=${q.limit}${q.kind ? `&kind=${q.kind}` : ""}` +
    `${q.minArea != null ? `&minArea=${q.minArea}` : ""}${q.maxArea != null ? `&maxArea=${q.maxArea}` : ""}` +
    `${q.cursor ? `&cursor=${encodeURIComponent(q.cursor)}` : ""}`,
  rentHistory: (rentId: string) => `/v1/rents/${rentId}/history`,
  complex: (key: string) => `/v1/complexes/${key}`,
  index: (regionId: string) => `/v1/index?regionId=${regionId}`,
  indexSummary: () => "/v1/index/summary",
  // 매매(기본)는 예전 경로 그대로 — 경로 문자열이 클라이언트 캐시 키라 같은 조회는 같은 문자열이어야 한다
  qualitySummary: (s: Source = "rtms") => `/v1/quality/summary${src(s, "?")}`,
  qualityRollup: (from: string, to: string, s: Source = "rtms") => `/v1/quality/rollup?from=${from}&to=${to}${src(s, "&")}`,
  qualityGrid: (from: string, to: string, sido: string, s: Source = "rtms") =>
    `/v1/quality/partitions?from=${from}&to=${to}&sido=${sido}${src(s, "&")}`,
  qualityPartition: (sgg: string, ym: string, s: Source = "rtms") => `/v1/quality/partitions/${sgg}/${ym}${src(s, "?")}`,
  qualityLatency: (from: string, to: string) => `/v1/quality/latency?from=${from}&to=${to}`,
  opsStatus: () => "/v1/ops/status",
  opsErrors: (hours: string, includeResolved: boolean, includeCleared: boolean) =>
    `/v1/ops/errors?hours=${hours}&includeResolved=${includeResolved}&includeCleared=${includeCleared}`,
  opsErrorsClear: () => "/v1/ops/errors/clear",
  opsConnectivity: () => "/v1/ops/connectivity",
  myUsage: (days: number) => `/v1/me/usage?days=${days}`,
  myUsageMonthly: (months: number) => `/v1/me/usage/monthly?months=${months}`,
  plans: () => "/v1/plans",
};
