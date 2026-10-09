-- 수집→발행 지연 L3 (ADR-046): 달 발행 때, 직전 기록 발행 이후 새로 수집돼 이번에 처음 서빙에 나가는 파티션마다 한 줄.
-- 기능 시작 후부터만 쌓인다 (그 전 발행 시각과 수집 시각의 짝은 남아 있지 않다). 파이프라인은 더하기만.
CREATE TABLE ops.publish_lag (
    publish_id    uuid         NOT NULL REFERENCES ops.dataset_version (publish_id),
    sgg_cd        char(5)      NOT NULL,
    deal_ym       char(6)      NOT NULL,
    fetched_at    timestamptz  NOT NULL,   -- 이번에 반영되는 수집분의 수집 시각 (ingest_partition.last_changed_at)
    published_at  timestamptz  NOT NULL,
    PRIMARY KEY (publish_id, sgg_cd, deal_ym)
);
CREATE INDEX publish_lag_time ON ops.publish_lag (published_at);

GRANT SELECT, INSERT ON ops.publish_lag TO pipeline;
GRANT SELECT ON ops.publish_lag TO api_app;
