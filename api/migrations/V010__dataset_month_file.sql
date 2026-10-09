-- 발행 기록과 달별 스냅숏 파일 (ADR-044, 데이터 제품 설계 계획서 1장).
-- 달을 발행할 때마다 그 달의 서빙 내용(trade_current)을 Parquet 파일로 남기고, 버전 기록과 같은 트랜잭션으로 등록한다.
-- 버전 V 의 데이터 = 달마다 V 이전에 기록된 가장 최근 파일. 버전 지정 내보내기(F3)와 변경분 피드(F2)의 기준이 된다.
-- publish_id: 버전 문자열은 발행 전에 정해지고 실패 후 재시도하면 같은 값이 다시 나온다 → 시도마다 고유한 uuid 를 붙인다.
ALTER TABLE ops.dataset_version ADD COLUMN publish_id uuid UNIQUE;

CREATE TABLE ops.dataset_month_file (
    publish_id  uuid         NOT NULL REFERENCES ops.dataset_version (publish_id),
    deal_ym     char(6)      NOT NULL,
    object_key  text         NOT NULL,   -- snapshots 버킷 안 경로
    rows        int          NOT NULL,
    bytes       bigint       NOT NULL,
    sha256      char(64)     NOT NULL,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    deleted_at  timestamptz,             -- 보관 정책으로 파일을 지운 시각 (기록은 남긴다)
    PRIMARY KEY (publish_id, deal_ym)
);
CREATE INDEX dataset_month_file_month ON ops.dataset_month_file (deal_ym);

-- 파일 기록은 더하기만, 정리 작업은 지운 시각만 채운다
GRANT SELECT, INSERT ON ops.dataset_month_file TO pipeline;
GRANT UPDATE (deleted_at) ON ops.dataset_month_file TO pipeline;
GRANT SELECT ON ops.dataset_month_file TO api_app;

-- 내보내기: 어느 버전에서 뽑았는지, 결과 파일 해시
ALTER TABLE api.export_job ADD COLUMN dataset_version varchar(40), ADD COLUMN sha256 char(64);
