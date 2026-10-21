// 공개 API 응답 타입 (서버 응답 모양 그대로 — 화면 계산은 domain/, 요청은 client.ts, 경로는 endpoints.ts)
export type Region = { sggCd: string; sidoCd: string; sidoName: string; name: string; fullName: string };

export type Trade = {
  tradeId: string; dealDate: string; priceManwon: number; areaM2: number; floor: number | null;
  pricePerM2: number | null; cancelled: boolean; cancelDate: string | null; registeredDate: string | null;
  aptDong: string | null; dealKind: string | null; sellerType: string | null; buyerType: string | null;
  outlier: boolean; version: number; missingSince: string | null;
  complex: { complexKey: string; aptName: string; umdName: string; jibun: string | null; buildYear: number | null };
};

export type MonthRow = {
  dealYm: string; reported: number; trades: number; cancelled: number; sampleSize: number; outliers: number;
  p25PricePerM2: number | null; medianPricePerM2: number | null; p75PricePerM2: number | null;
  provisional?: boolean; lowSample?: boolean;
};

export type AreaSummary = {
  regionId: string; trades: number; cancelled: number; cancelRate: number | null; median: number | null;
  p25: number | null; p75: number | null; sample: number; tradesMoM: number | null; tradesYoY: number | null;
  medianYoY: number | null; spark: { months: string[]; trades: number[]; median: (number | null)[] };
};

export type SggSummary = {
  sggCd: string; trades: number; cancelled: number; cancelRate: number | null; median: number | null;
  sample: number; lowSample: boolean; medianYoY: number | null; tradesYoY: number | null; tradesMoM: number | null;
  /** 전세가율(%) — 그 달까지 3개월 창, 양쪽 표본 5건 이상일 때만. 창 안에 매매·전월세 자료가 발행된 적이 없는 달이 있으면 null (ADR-050) */
  jeonseRatio?: number | null;
};

export type Overview = {
  month: string; provisional: boolean; available: { from: string; to: string; default: string };
  nation: AreaSummary | null; sido: AreaSummary[]; sgg: SggSummary[];
  rankings: { volume: SggSummary[]; gainers: SggSummary[]; losers: SggSummary[] };
  definitions: Record<string, string>; disclaimer: string; datasetVersion: string; dataAsOf: string | null;
};

export type Available = { from: string; to: string; default: string };
export type Ticker = { month: string; items: TickerItem[]; available: Available | null; datasetVersion: string; dataAsOf: string | null };

export type TickerItem = {
  key: string; label: string; value: number | null; unit: string; change: number | null;
  changeBasis: string | null; provisional?: boolean;
};

export type IndexPoint = { period: string; value: number; provisional: boolean; mom: number | null; yoy: number | null };
/** /v1/index/summary 항목 — 최상위 값은 가장 최근 달(잠정일 수 있음), confirmed 는 잠정이 아닌 가장 최근 달(대표값). */
export type IndexSummaryItem = IndexPoint & {
  regionId: string; confirmed: IndexPoint | null; spark: number[];
  corrMoM: number | null; directionMatch: number | null; months: number;
};

/** 응답 머리에 붙는 자료 버전 (발행본 단위) */
export type Versioned = { datasetVersion: string; dataAsOf: string | null };

// ───── 지역 ─────
export type RegionMonths = { region: { sggCd: string; name: string; fullName: string }; items: MonthRow[]; notes: string[]; disclaimer: string };
export type PriceDistribution = {
  month: string; provisional: boolean;
  stats: { reported: number; cancelled: number; sample: number; p05: number | null; p25: number | null; median: number | null; p75: number | null; p95: number | null } | null;
  histogram: { lo: number; hi: number; n: number }[]; binWidth: number;
  byArea: { band: string; n: number; medianPpm2: number | null; medianPrice: number | null }[];
  byFloor: { band: string; n: number; medianPpm2: number | null }[];
  /** [전용면적, 거래금액(만원), 층, 해제(0/1), 이상치(0/1)] */
  points: [number, number, number | null, number, number][]; notes: string[];
};
export type RegionComplex = { complexKey: string; aptName: string; umdName: string; buildYear: number | null; trades: number; cancelled: number; medianPpm2: number | null; lastDate: string; lastPrice: number; lastArea: number };

// ───── 거래·단지 ─────
export type TradeSummary = { count: number; cancelled: number; medianPpm2: number | null; medianPrice: number | null };
export type TradePage = { items: Trade[]; summary: TradeSummary | null; page: { nextCursor: string | null } };
export type TradeVersion = { version: number; validFrom: string; validTo: string | null; current: boolean; changes: { field: string; from: unknown; to: unknown }[] };
export type ComplexDetail = {
  complex: { complexKey: string; sggCd: string; umdName: string; jibun: string | null; aptName: string; buildYear: number | null;
             /** 토지임대부 — 매매 원천에만 있는 항목이라 전월세에만 나온 단지는 null(미상) */
             landLeasehold: boolean | null; firstSeen: string; validTrades: number };
  recentTrades: Trade[];
  /** [계약일, 전용면적, 층, 거래금액(만원), m²당(만원), 해제(0/1), 이상치(0/1)] */
  history: [string, number, number | null, number, number, number, number][];
  /** 전월세 요약 (ADR-050) — 전월세가 아직 발행되지 않았으면 없음.
   *  전월세에만 있는 단지는 validTrades=0 · recentTrades=[] · history=[] 이고 이 블록만 있다 */
  rent?: ComplexRent;
};
export type ComplexHit = { complexKey: string; sggCd: string; umdName: string; aptName: string; buildYear: number | null; validTrades: number };

// ───── 전월세 (ADR-050) ─────
export type RentMonthRow = {
  dealYm: string; contracts: number; jeonse: number; wolse: number; renewals: number; newContracts: number; typeUnknown: number;
  renewalRightUsed: number; jeonseSample: number; outliers: number;
  jeonseP25PerM2: number | null; jeonseMedianPerM2: number | null; jeonseP75PerM2: number | null;
  wolseMedianRent: number | null; wolseMedianDeposit: number | null; provisional?: boolean; lowSample?: boolean;
};
/** uncollectedMonths('YYYY-MM') = 그 시군구 전월세 자료가 아직 발행된 적이 없는 달(수집 전·첫 수집 실패·첫 발행 전). items 에도 이 목록에도 없는 달은 계약 0건 */
export type RentMonths = { region: { sggCd: string; name: string; fullName: string }; items: RentMonthRow[]; uncollectedMonths: string[];
  definitions: Record<string, string>; notes: string[]; disclaimer: string };
export type JeonseRatioRow = {
  dealYm: string; windowFrom: string; tradeMedianPerM2: number | null; tradeSample: number;
  jeonseMedianPerM2: number | null; jeonseSample: number; ratioPct: number | null; provisional?: boolean;
  /** 창의 모든 달이 매매·전월세 모두 발행된 적이 있는지 — false 면 ratioPct·tradeMedianPerM2·jeonseMedianPerM2 = null */
  complete: boolean;
};
export type JeonseRatio = { region: { sggCd: string; name: string }; items: JeonseRatioRow[];
  definition: { windowMonths: number; minSample: number; unit: string }; notes: string[] };
export type Rent = {
  rentId: string; dealDate: string; kind: "jeonse" | "wolse"; depositManwon: number; monthlyRentManwon: number;
  areaM2: number; floor: number | null; depositPerM2: number | null; contractType: string | null; contractTerm: string | null;
  renewalRightUsed: string | null; previousDepositManwon: number | null; previousMonthlyRentManwon: number | null;
  outlier: boolean; version: number; missingSince: string | null;
  complex: { complexKey: string; aptName: string; umdName: string; jibun: string | null; buildYear: number | null };
};
export type RentSummary = { count: number; jeonse: number; wolse: number; jeonseMedianDeposit: number | null;
  jeonseMedianPerM2: number | null; wolseMedianRent: number | null };
export type RentPage = { items: Rent[]; summary: RentSummary | null; page: { nextCursor: string | null }; definitions: Record<string, string> };
export type RentVersion = { version: number; validFrom: string; validTo: string | null; current: boolean; changes: { field: string; from: unknown; to: unknown }[] };
export type RentDistribution = {
  month: string; provisional?: boolean;
  stats: { contracts: number; jeonse: number; wolse: number; jeonseSample: number; p05: number | null; p25: number | null;
           median: number | null; p75: number | null; p95: number | null; wolseMedianRent: number | null; wolseMedianDeposit: number | null } | null;
  histogram: { lo: number; hi: number; n: number }[]; binWidth?: number;
  byArea: { band: string; jeonse: number; jeonseMedianDeposit: number | null; jeonseMedianPerM2: number | null;
            wolse: number; wolseMedianRent: number | null; wolseMedianDeposit: number | null }[];
  notes?: string[];
};
export type ComplexRent = {
  /** complete = 단지 시군구에서 창(12개월)의 모든 달이 매매·전월세 모두 발행된 적이 있는지 — false 면 byArea[].ratioPct 가 모두 null
   *  (계약 수·중앙값·매매 표본은 발행된 계약 기준 그대로) */
  window: { from: string; to: string; complete: boolean }; contracts: number;
  byArea: { band: string; jeonse: number; jeonseMedianDeposit: number | null; jeonseMedianPerM2: number | null;
            wolse: number; wolseMedianRent: number | null; wolseMedianDeposit: number | null;
            tradeSample: number; tradeMedianPerM2: number | null; ratioPct: number | null }[];
  recent: Rent[]; notes: string[];
};

// ───── 시장·지도 ─────
export type GeoSgg = { type: "FeatureCollection"; source: string; features: { id: string; properties: { sggCd: string; name?: string }; geometry: unknown }[] };

// ───── 지수 ─────
export type IndexSeriesPoint = { period: string; value: number; ciLow: number; ciHigh: number; nObs: number; provisional?: boolean };
export type RegionIndex = {
  regionId: string; method: string; base: string; series: IndexSeriesPoint[];
  reference: { source: string | null; series: { period: string; value: number }[] };
  validation: { reference: string; corrMoM: number | null; directionMatch: number | null; months: number; window: string } | null;
  disclaimer: string;
};

// ───── 품질 ─────
export type FailedCheck = { asset: string; partition: string | null; check: string; severity: string; blocking: boolean; metric: unknown; at: string; resolved: boolean };
export type QualitySummary = {
  source?: "rtms" | "rent";
  partitions: Record<string, number>;
  /** 마지막 적재 원본의 원천 스키마 판별 파티션 수 ('unrecorded' = 판 기록 이전 적재, 'empty' = 마지막 응답 0건이라 판을 가릴 수 없음) */
  schemaVersions?: Record<string, number>;
  freshness: { lastFetchedAt: string | null; lastChangedAt: string | null };
  /** 고른 원천의 마지막 발행 */
  dataset: { version: string; publishedAt: string; dataAsOf: string } | null;
  failedChecks7d: FailedCheck[];
};
/** 시도 → 계약월(YYYYMM) → 상태 → 시군구 수 */
export type QualityRollup = { cells: Record<string, Record<string, Record<string, number>>> };
/** 시군구 → 계약월(YYYYMM) → [상태, 원천 건수] */
export type QualityGrid = { cells: Record<string, Record<string, [string, number | null]>> };
export type QualityPartition = {
  partition: { sggCd: string; dealYm: string; status: string; attempts: number; rows: number | null; rowsPrev: number | null; observations: number;
               lastFetchedAt: string | null; lastChangedAt: string | null; nextDueAt: string | null; lastError: string | null };
  checks: { asset: string; name: string; passed: boolean; blocking: boolean; severity: string; metric: unknown; at: string }[];
  lineage: string[];
};
/** 원천 반영 지연 (ADR-046). distribution = [지연(일) 또는 구간 상한(분, null=그 이상), 건수] */
export type LatencyMetric = {
  unit: "day" | "minute"; n: number; p50: number | null; p90: number | null; p99: number | null;
  distribution: [number | null, number][]; excluded: Record<string, number>; measuredSince: string | null;
};
export type QualityLatency = { from: string; to: string; metrics: Record<"L1" | "L2" | "L3", LatencyMetric> };

// ───── 운영 ─────
export type OpsJob = {
  runId: string; job: string; jobLabel: string; partition: string | null; priority: string | null; priorityLabel: string;
  trigger: string; status: string; requestedAt: string | null; startedAt: string | null; endedAt: string | null; durationS: number | null;
};
export type OpsSchedule = { name: string; label: string; type: "schedule" | "sensor"; status: string; rule: string; nextAt: string | null;
  lastTick: { status: string; at: string | null; skipReason: string; error: string | null; runs: number } | null };
export type OpsStatus = {
  pipeline: { available: boolean; reason?: string };
  summary: {
    partitions: Record<string, number>; totalPartitions: number; publishedMonths: number; publishPending: number;
    budget: { day: string; limit: number; cap: number; used: number; byPriority: Record<string, number>; exhaustedReason: string | null; exhaustedAt: string | null; stopLabel?: string | null };
    backfillEstimate: { remainingCalls: number; days: number | null; basis: string };
    lastPublish: { version: string; at: string } | null;
    /** 전월세 수집 (ADR-050) — 상태·예산은 원천별 */
    rent?: { partitions: Record<string, number>; totalPartitions: number; publishedMonths: number; publishPending: number;
             budget: OpsStatus["summary"]["budget"]; backfillEstimate: OpsStatus["summary"]["backfillEstimate"] };
  };
  jobs: { active: OpsJob[]; recent: OpsJob[] }; schedules: OpsSchedule[]; serverTime: string;
};
export type LogEntry = { id: string; at: string | null; level: "ERROR" | "WARN"; source: string; where: string; message: string; detail: string; ref: string | null; resolved: boolean; cleared?: boolean };
export type OpsErrors = { window: { hours: number; since: string }; counts: Record<string, number>; entries: LogEntry[]; truncated: boolean;
  apiErrorSummary: { route: string; status: number; count: number }[]; notes: string[]; cleared: { at: string; by: string | null } | null };
export type Connectivity = {
  checkedAt: string; ok: boolean; notes: string[];
  items: { key: string; name: string; role: string; ok: boolean; latencyMs: number | null; note: string | null; error: string | null }[];
  sources: { key: string; name: string; lastSuccessAt: string | null; state: string; detail: string | null }[];
};
/** 플랜 한도 (ADR-047). bulk = 대량 내보내기·변경분 피드 */
export type PlanView = { planId: string; rpm: number; dailyRows: number; maxRangeMonths: number | null; maxPageSize: number; bulk: boolean };
export type Plans = { plans: PlanView[] };
/** limits·today 는 이 키 기준, items 는 고객(모든 키 합) 기준 */
export type MyUsage = { clientId: string; plan: string; limits: PlanView; today: { rowsUsed: number; rowsLeft: number };
  items: { day: string; requests: number; rows: number; errors: number; p95Ms: number }[] };
export type MonthlyUnits = { month: string; requests: number; errors: number; rateLimited: number; chargedRows: number; exports: number; exportRows: number };
export type MyUsageMonthly = { clientId: string; basis: "client"; items: MonthlyUnits[] };
