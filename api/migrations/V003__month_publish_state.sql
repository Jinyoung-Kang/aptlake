-- 월 단위 발행 상태: silver 에 반영됐지만 ClickHouse 발행 전인 달을 추적한다.
-- (실행이 silver 이후·발행 전에 죽으면, 다음 수집은 원본이 같아 하위 단계를 건너뛰므로 영원히 미발행으로 남는 문제)
CREATE TABLE ops.month_state (
    deal_ym            char(6) PRIMARY KEY CHECK (deal_ym ~ '^[0-9]{6}$'),
    needs_publish      boolean     NOT NULL DEFAULT false,
    silver_snapshot    bigint,
    merged_at          timestamptz,
    published_version  varchar(40),
    published_at       timestamptz
);
CREATE INDEX month_state_pending ON ops.month_state (deal_ym) WHERE needs_publish;

GRANT SELECT, INSERT, UPDATE ON ops.month_state TO pipeline;
GRANT SELECT ON ops.month_state TO api_app;

-- 이미 silver 에 반영된 달은 한 번 다시 발행되도록 표시 (대조 후 교체라 중복 발행도 안전)
INSERT INTO ops.month_state (deal_ym, needs_publish)
SELECT DISTINCT deal_ym, true FROM ops.ingest_partition WHERE status = 'MERGED';
