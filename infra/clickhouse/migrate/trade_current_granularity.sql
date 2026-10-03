-- trade_current 인덱스 입도 8192 → 1024 (ADR-038). ch-migrate 가 표 설정이 아직 1024 가 아닐 때만 한 번 실행한다.
-- 수집 중에 배포해도 발행이 빠지지 않도록 스테이징을 먼저 지운다: 그사이 발행은 교체 단계에서 실패하고,
-- 그 달은 needs_publish 가 남아 센서가 발행만 다시 한다. 실패하면 ch-migrate 가 스키마를 다시 적용해 스테이징을 되살린다.
-- 행은 그대로 옮기고, 행 수·행 체크섬이 같을 때만 EXCHANGE TABLES 로 원자적으로 바꾼다 (다르면 여기서 멈추고 원래 표 그대로).
DROP TABLE IF EXISTS aptlake.trade_current_staging;
DROP TABLE IF EXISTS aptlake.trade_current_g1024_new;
CREATE TABLE aptlake.trade_current_g1024_new AS aptlake.trade_current
ENGINE = MergeTree PARTITION BY toYYYYMM(deal_date) ORDER BY (sgg_cd, deal_date, trade_id)
SETTINGS index_granularity = 1024;
INSERT INTO aptlake.trade_current_g1024_new SELECT * FROM aptlake.trade_current;
SELECT throwIf(
    (SELECT (count(), sum(cityHash64(toString(tuple(*))))) FROM aptlake.trade_current_g1024_new)
    != (SELECT (count(), sum(cityHash64(toString(tuple(*))))) FROM aptlake.trade_current),
    'trade_current 입도 이관: 행 수·체크섬이 다름 — 바꾸지 않음') FORMAT Null;
EXCHANGE TABLES aptlake.trade_current AND aptlake.trade_current_g1024_new;
DROP TABLE aptlake.trade_current_g1024_new;
-- 스테이징 파트가 REPLACE PARTITION 으로 그대로 붙으므로 스테이징도 같은 입도여야 새 발행분이 1024 로 들어온다
CREATE TABLE aptlake.trade_current_staging AS aptlake.trade_current;
