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
-- 연 단위 파티션 (ADR-037): 1.7만 행 표를 월로 나누면 12개월 질의가 파트 12개를 열어 질의당 CPU 가 약 5배.
-- 월 발행은 그 해 파티션 전체를 스테이징에 만들어 원자적으로 교체한다 (publish.py). 기존 월 파티션 표는 ../migrate/ 로 바꾼다.
) ENGINE = MergeTree PARTITION BY toYear(month) ORDER BY (sgg_cd, month);

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
-- 인덱스 입도 1024 (ADR-038): 질의 대부분이 (시군구, 한 달) ≈ 150행인데 기본 8192행 그래뉼이면 한 번에 1.7만 행을 읽는다.
-- 블룸 필터(GRANULARITY 4)도 4096행 단위로 좁아져 단지 질의가 덜 읽는다. 기존 8192 표는 ../migrate/ 로 바꾼다.
) ENGINE = MergeTree PARTITION BY toYYYYMM(deal_date) ORDER BY (sgg_cd, deal_date, trade_id)
  SETTINGS index_granularity = 1024;

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

-- 원천 반영 지연 분포 (ADR-046): dbt gold.source_latency 를 매일 통째로 교체 (차원 발행).
CREATE TABLE IF NOT EXISTS aptlake.source_latency
(
    metric         LowCardinality(String),
    observed_date  Date,
    sido_cd        LowCardinality(String),
    reason         LowCardinality(String),
    lag_days       Nullable(Int32),
    n              UInt64
) ENGINE = MergeTree ORDER BY (metric, observed_date, sido_cd);

-- 변경분 피드 (ADR-045): 달 발행마다 '마지막으로 기록된 그 달 파일'과 새 값을 비교한 결과. 행에 그 발행 시점의 거래 값을
-- 함께 둔다(나중에 trade_current 가 또 바뀌어도 피드는 그때 값을 준다). 버전 기록(PostgreSQL)이 커밋된 publish_id 만 보인다.
-- 보존 90일 — API 설정 change_retention_days 와 같아야 한다 (시험으로 확인).
CREATE TABLE IF NOT EXISTS aptlake.trade_change
(
    publish_id       UUID,
    deal_ym          UInt32,
    trade_id         String,
    change           Enum8('added' = 1, 'removed' = 2, 'updated' = 3, 'missing' = 4, 'restored' = 5),
    changed_fields   Array(LowCardinality(String)),
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
    created_at       DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree PARTITION BY toYYYYMM(created_at) ORDER BY (publish_id, trade_id)
  TTL toDateTime(created_at) + INTERVAL 90 DAY DELETE;

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
