-- 운영(ops)·API 메타(api) 스키마. 소유자 migrator, 앱 역할에는 필요한 권한만 부여 (NFR-06).
CREATE SCHEMA ops;
CREATE SCHEMA api;

-- ───────────── ops: 파이프라인 상태 ─────────────
CREATE TABLE ops.region (
    sgg_cd      char(5) PRIMARY KEY,
    sido_cd     char(2)      NOT NULL,
    sido_nm     varchar(40)  NOT NULL,
    sgg_nm      varchar(60)  NOT NULL,
    full_nm     varchar(120) NOT NULL,
    is_leaf     boolean      NOT NULL,    -- 하위 일반구가 있는 시(예: 성남시)는 false → 수집 대상 아님
    active      boolean      NOT NULL DEFAULT true,
    source      varchar(40)  NOT NULL,
    fetched_at  timestamptz  NOT NULL
);

-- 파티션 상태 머신 (기획서 6-3)
CREATE TABLE ops.ingest_partition (
    sgg_cd           char(5)     NOT NULL,
    deal_ym          char(6)     NOT NULL CHECK (deal_ym ~ '^[0-9]{6}$'),
    status           varchar(12) NOT NULL DEFAULT 'PENDING'
                     CHECK (status IN ('PENDING','FETCHING','RETRY','LOADED','MERGED','QUARANTINED')),
    attempts         int         NOT NULL DEFAULT 0,
    rows_last        int,
    rows_prev        int,
    payload_sha256   char(64),
    last_ingest_id   varchar(40),
    last_fetched_at  timestamptz,
    last_changed_at  timestamptz,           -- 원본 해시가 바뀐 마지막 시각
    fetch_count      int         NOT NULL DEFAULT 0,   -- 성공 수집(관측) 횟수 — 미관측 연속 횟수 판단 기준
    next_due_at      timestamptz NOT NULL DEFAULT now(),
    last_error       text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sgg_cd, deal_ym)
);
CREATE INDEX ingest_partition_due ON ops.ingest_partition (status, next_due_at);
CREATE INDEX ingest_partition_ym ON ops.ingest_partition (deal_ym, status);

-- 일일 호출 예산 (FR-103). Redis 카운터가 원자적 차감을 하고, 이 표는 영속 기록이다.
CREATE TABLE ops.api_budget (
    day          date        NOT NULL,
    source       varchar(12) NOT NULL,
    limit_calls  int         NOT NULL,
    used_calls   int         NOT NULL DEFAULT 0,
    by_priority  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (day, source)
);

CREATE TABLE ops.dq_result (
    check_id    bigserial PRIMARY KEY,
    asset       varchar(60) NOT NULL,
    partition   varchar(20),
    check_name  varchar(60) NOT NULL,
    passed      boolean     NOT NULL,
    severity    varchar(6)  NOT NULL CHECK (severity IN ('ERROR','WARN')),
    blocking    boolean     NOT NULL DEFAULT false,
    metric      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    run_id      varchar(40),
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dq_result_partition ON ops.dq_result (partition, at DESC);
CREATE INDEX dq_result_failed ON ops.dq_result (at DESC) WHERE NOT passed;

-- 발행 이력: 데이터셋 버전 = 하나의 원자적 ClickHouse 발행 (FR-503, NFR-02 계보)
CREATE TABLE ops.dataset_version (
    version       varchar(40) PRIMARY KEY,
    published_at  timestamptz NOT NULL DEFAULT now(),
    data_as_of    timestamptz NOT NULL,
    partitions    text[]      NOT NULL,
    snapshots     jsonb       NOT NULL,     -- {"bronze": id, "silver": id, "gold.region_month": id, ...}
    row_counts    jsonb       NOT NULL,
    run_id        varchar(40)
);
CREATE INDEX dataset_version_time ON ops.dataset_version (published_at DESC);

-- ───────────── api: 키·플랜·사용 ─────────────
CREATE TABLE api.plan (
    plan_id           varchar(12) PRIMARY KEY,
    rpm               int     NOT NULL,
    daily_rows        int     NOT NULL,
    max_range_months  int,             -- NULL = 제한 없음
    max_page_size     int     NOT NULL,
    allow_bulk        boolean NOT NULL
);
INSERT INTO api.plan VALUES
    ('anonymous',  20,    5000,   12,  100, false),
    ('free',       60,   50000,   24,  200, false),
    ('pro',       600, 2000000, NULL, 1000, true);

CREATE TABLE api.client (
    client_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(80) NOT NULL,
    plan_id     varchar(12) NOT NULL REFERENCES api.plan,
    status      varchar(10) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE api.api_key (
    key_id        char(12) PRIMARY KEY,
    client_id     uuid        NOT NULL REFERENCES api.client,
    secret_hmac   char(64)    NOT NULL,      -- HMAC-SHA256(pepper, secret). 원문은 저장하지 않는다
    scopes        text[]      NOT NULL CHECK (scopes <@ ARRAY['read','bulk','admin']::text[]),
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    last_used_at  timestamptz
);
CREATE INDEX api_key_client ON api.api_key (client_id);

CREATE TABLE api.export_job (
    job_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id    uuid        NOT NULL REFERENCES api.client,
    key_id       char(12)    NOT NULL,
    status       varchar(10) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
    params       jsonb       NOT NULL,
    object_key   text,
    rows         int,
    error        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz
);
CREATE INDEX export_job_queue ON api.export_job (created_at) WHERE status = 'queued';

-- 감사 로그: 앱 역할은 INSERT·SELECT 만 (수정·삭제 불가 = append-only)
CREATE TABLE api.audit_log (
    audit_id   bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      varchar(40) NOT NULL,
    action     varchar(40) NOT NULL,
    target     varchar(80),
    client_ip  inet,
    detail     jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ───────────── 권한 ─────────────
GRANT USAGE ON SCHEMA ops TO pipeline, api_app;
GRANT USAGE ON SCHEMA api TO api_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ops TO pipeline;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops TO pipeline;

GRANT SELECT ON ALL TABLES IN SCHEMA ops TO api_app;
-- 격리 파티션 재시도(POST /admin/partitions/.../retry)에 필요한 열만 수정 가능
GRANT UPDATE (status, attempts, next_due_at, last_error, updated_at) ON ops.ingest_partition TO api_app;

GRANT SELECT ON api.plan TO api_app;
GRANT SELECT, INSERT, UPDATE ON api.client, api.api_key, api.export_job TO api_app;
GRANT SELECT, INSERT ON api.audit_log TO api_app;
GRANT USAGE ON SEQUENCE api.audit_log_audit_id_seq TO api_app;
