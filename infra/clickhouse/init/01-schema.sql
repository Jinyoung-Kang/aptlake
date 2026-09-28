-- 서빙 계층 스키마 (Gold 발행 대상 + 사용량 계측). 멱등 (IF NOT EXISTS).
-- 발행 테이블마다 동일 구조의 *_staging 을 두고, 파티션 단위 REPLACE PARTITION 으로 원자적 교체한다.
CREATE DATABASE IF NOT EXISTS aptlake;

CREATE TABLE IF NOT EXISTS aptlake.region
(
    sgg_cd   LowCardinality(String),
    sido_cd  LowCardinality(String),
    sido_nm  String,
    sgg_nm   String,
    full_nm  String
) ENGINE = MergeTree ORDER BY sgg_cd;

CREATE TABLE IF NOT EXISTS aptlake.region_month
(
    sgg_cd       LowCardinality(String),
    month        Date,
    reported     UInt32,
    trades       UInt32,
    cancelled    UInt32,
    priced       UInt32,
    outliers     UInt32,
    p25_ppm2     Nullable(Float64),
    median_ppm2  Nullable(Float64),
    p75_ppm2     Nullable(Float64),
    low_sample   UInt8,
    dataset_ver  LowCardinality(String)
) ENGINE = MergeTree PARTITION BY toYYYYMM(month) ORDER BY (sgg_cd, month);

CREATE TABLE IF NOT EXISTS aptlake.rollup_month
(
    region_id    LowCardinality(String),   -- 시도 2자리, 전국 '00'
    level        LowCardinality(String),   -- 'sido' | 'nation'
    month        Date,
    reported     UInt32,
    trades       UInt32,
    cancelled    UInt32,
    priced       UInt32,
    outliers     UInt32,
    p25_ppm2     Nullable(Float64),
    median_ppm2  Nullable(Float64),
    p75_ppm2     Nullable(Float64),
    low_sample   UInt8,
    dataset_ver  LowCardinality(String)
) ENGINE = MergeTree PARTITION BY toYYYYMM(month) ORDER BY (region_id, month);

CREATE TABLE IF NOT EXISTS aptlake.trade_current
(
    trade_id         String,
    sgg_cd           LowCardinality(String),
    deal_date        Date,
    complex_key      String,
    apt_nm           String,
    umd_nm           LowCardinality(String),
    jibun            String,
    area_m2          Decimal(9, 4),
    floor            Nullable(Int16),
    price_manwon     UInt32,
    ppm2             Float64,
    is_cancelled     UInt8,
    cancel_date      Nullable(Date),
    registered_date  Nullable(Date),
    apt_dong         String,
    deal_kind        LowCardinality(String),
    seller_type      LowCardinality(String),
    buyer_type       LowCardinality(String),
    build_year       Nullable(UInt16),
    is_outlier       UInt8,
    version          UInt16,
    valid_from       DateTime64(3, 'UTC'),
    missing_since    Nullable(DateTime64(3, 'UTC')),
    INDEX idx_complex complex_key TYPE bloom_filter GRANULARITY 4
) ENGINE = MergeTree PARTITION BY toYYYYMM(deal_date) ORDER BY (sgg_cd, deal_date, trade_id);

CREATE TABLE IF NOT EXISTS aptlake.trade_version
(
    trade_id         String,
    deal_date        Date,
    version          UInt16,
    valid_from       DateTime64(3, 'UTC'),
    valid_to         Nullable(DateTime64(3, 'UTC')),
    is_current       UInt8,
    is_cancelled     UInt8,
    cancel_date      Nullable(Date),
    registered_date  Nullable(Date),
    apt_dong         String,
    deal_kind        LowCardinality(String),
    seller_type      LowCardinality(String),
    buyer_type       LowCardinality(String)
) ENGINE = MergeTree PARTITION BY toYYYYMM(deal_date) ORDER BY (trade_id, valid_from);

CREATE TABLE IF NOT EXISTS aptlake.complex
(
    complex_key     String,
    sgg_cd          LowCardinality(String),
    umd_nm          String,
    jibun           String,
    apt_nm          String,
    build_year      Nullable(UInt16),
    land_leasehold  UInt8,
    first_seen      Date,
    trades          UInt32
) ENGINE = MergeTree ORDER BY complex_key;

CREATE TABLE IF NOT EXISTS aptlake.price_index
(
    region_id    LowCardinality(String),
    method       LowCardinality(String),
    period       Date,
    index_value  Float64,
    ci_low       Float64,
    ci_high      Float64,
    n_obs        UInt32,
    model_ver    LowCardinality(String)
) ENGINE = MergeTree ORDER BY (region_id, method, period);

CREATE TABLE IF NOT EXISTS aptlake.index_reference
(
    region_id    LowCardinality(String),
    period       Date,
    value        Float64,
    source       LowCardinality(String)
) ENGINE = MergeTree ORDER BY (region_id, period);

CREATE TABLE IF NOT EXISTS aptlake.index_validation
(
    region_id        LowCardinality(String),
    method           LowCardinality(String),
    reference        String,
    corr_mom         Nullable(Float64),
    direction_match  Nullable(Float64),
    n_months         UInt32,
    window_from      Date,
    window_to        Date
) ENGINE = MergeTree ORDER BY (region_id, method);

CREATE TABLE IF NOT EXISTS aptlake.usage_event
(
    at          DateTime64(3, 'UTC'),
    key_id      LowCardinality(String),
    client_id   String,
    plan_id     LowCardinality(String),
    route       LowCardinality(String),
    status      UInt16,
    rows        UInt32,
    latency_ms  UInt32,
    trace_id    String,
    error       LowCardinality(String) DEFAULT ''  -- 5xx 의 원인 분류(예외 클래스·일시 장애 사유). 컨테이너를 다시 만들면 서버 로그가 사라져도 남도록
) ENGINE = MergeTree PARTITION BY toYYYYMM(at) ORDER BY (client_id, at)
  TTL toDateTime(at) + INTERVAL 180 DAY DELETE;
-- 이미 만들어진 표에도 (ch-migrate 는 기동마다 이 파일을 멱등 적용)
ALTER TABLE aptlake.usage_event ADD COLUMN IF NOT EXISTS error LowCardinality(String) DEFAULT '' AFTER trace_id;

-- 발행 스테이징 (같은 구조)
CREATE TABLE IF NOT EXISTS aptlake.region_month_staging  AS aptlake.region_month;
CREATE TABLE IF NOT EXISTS aptlake.rollup_month_staging  AS aptlake.rollup_month;
CREATE TABLE IF NOT EXISTS aptlake.trade_current_staging AS aptlake.trade_current;
CREATE TABLE IF NOT EXISTS aptlake.trade_version_staging AS aptlake.trade_version;
