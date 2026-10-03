-- region_month 월 파티션 → 연 파티션 (ADR-037). ch-migrate 가 파티션 키가 아직 toYYYYMM(month) 일 때만 한 번 실행한다.
-- 행은 그대로 옮기고, 행 수·합계가 같을 때만 EXCHANGE TABLES 로 원자적으로 바꾼다 (다르면 여기서 멈추고 원래 표 그대로).
-- 그사이 발행이 끼면 그 달이 빠질 수 있으므로 수집이 멈춘 상태에서 배포한다 (운영 절차서 '코드 교체').
DROP TABLE IF EXISTS aptlake.region_month_yearly_new;
CREATE TABLE aptlake.region_month_yearly_new AS aptlake.region_month
ENGINE = MergeTree PARTITION BY toYear(month) ORDER BY (sgg_cd, month);
INSERT INTO aptlake.region_month_yearly_new SELECT * FROM aptlake.region_month;
SELECT throwIf(
    (SELECT (count(), sum(trades), sum(cancelled), sum(priced)) FROM aptlake.region_month_yearly_new)
    != (SELECT (count(), sum(trades), sum(cancelled), sum(priced)) FROM aptlake.region_month),
    'region_month 연 파티션 이관: 행 수·합계가 다름 — 바꾸지 않음') FORMAT Null;
EXCHANGE TABLES aptlake.region_month AND aptlake.region_month_yearly_new;
DROP TABLE aptlake.region_month_yearly_new;
-- 스테이징은 서빙 표와 파티션 키가 같아야 REPLACE PARTITION 을 할 수 있다 (평소 비어 있음)
DROP TABLE IF EXISTS aptlake.region_month_staging;
CREATE TABLE aptlake.region_month_staging AS aptlake.region_month;
