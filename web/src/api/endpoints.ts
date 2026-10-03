// 공개 API 경로 — 화면이 쓰는 모든 조회 경로를 한곳에서 만든다 (쿼리 이름·형식이 흩어지지 않게).
// 경로 문자열이 곧 클라이언트 캐시 키이므로 같은 조회는 늘 같은 문자열이 되어야 한다.

export type TradeQuery = {
  sgg: string; from: string; to: string; includeCancelled: boolean; limit: number;
  minArea?: number; maxArea?: number; cursor?: string;
};

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
  complex: (key: string) => `/v1/complexes/${key}`,
  index: (regionId: string) => `/v1/index?regionId=${regionId}`,
  indexSummary: () => "/v1/index/summary",
  qualitySummary: () => "/v1/quality/summary",
  qualityRollup: (from: string, to: string) => `/v1/quality/rollup?from=${from}&to=${to}`,
  qualityGrid: (from: string, to: string, sido: string) => `/v1/quality/partitions?from=${from}&to=${to}&sido=${sido}`,
  qualityPartition: (sgg: string, ym: string) => `/v1/quality/partitions/${sgg}/${ym}`,
  opsStatus: () => "/v1/ops/status",
  opsErrors: (hours: string, includeResolved: boolean, includeCleared: boolean) =>
    `/v1/ops/errors?hours=${hours}&includeResolved=${includeResolved}&includeCleared=${includeCleared}`,
  opsErrorsClear: () => "/v1/ops/errors/clear",
  opsConnectivity: () => "/v1/ops/connectivity",
  myUsage: (days: number) => `/v1/me/usage?days=${days}`,
};
