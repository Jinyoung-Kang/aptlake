-- 운영 상태에 원천 차원 (ADR-050): 아파트 매매(rtms)와 전월세(rent)를 같은 상태 머신·발행 대기·발행 기록으로 관리한다.
-- 원천 이름은 호출 예산(ops.api_budget.source, Redis budget:{source}:{day})과 같게 둔다 — 매매는 처음부터 'rtms'.
-- 기존 행은 모두 매매 → 기본값으로 채워진다. 키에 원천을 앞에 둬 (원천, 시군구, 계약월)·(원천, 계약월)로 유일.

ALTER TABLE ops.ingest_partition
    ADD COLUMN source varchar(12) NOT NULL DEFAULT 'rtms' CHECK (source IN ('rtms', 'rent')),
    -- 마지막 적재 원본의 원천 스키마 판 (매매 1 = 기본판, 2 = 상세판 — ADR-049). 적재 전·이 열 이전 적재는 NULL
    ADD COLUMN schema_ver smallint;
ALTER TABLE ops.ingest_partition DROP CONSTRAINT ingest_partition_pkey, ADD PRIMARY KEY (source, sgg_cd, deal_ym);
DROP INDEX ops.ingest_partition_ym;
CREATE INDEX ingest_partition_ym ON ops.ingest_partition (source, deal_ym, status);

ALTER TABLE ops.month_state ADD COLUMN source varchar(12) NOT NULL DEFAULT 'rtms' CHECK (source IN ('rtms', 'rent'));
ALTER TABLE ops.month_state DROP CONSTRAINT month_state_pkey, ADD PRIMARY KEY (source, deal_ym);

-- 발행 기록: 어느 서빙 데이터가 바뀐 발행인가. 매매 달·단지 차원·지수 = 'trade', 전월세 달 = 'rent'.
-- 수집→발행 지연(publish_lag)·계보 조회는 매매 달 발행끼리만 비교한다 (전월세 발행이 같은 달을 '발행'한 것으로 보이지 않게)
ALTER TABLE ops.dataset_version ADD COLUMN source varchar(12) NOT NULL DEFAULT 'trade' CHECK (source IN ('trade', 'rent'));
