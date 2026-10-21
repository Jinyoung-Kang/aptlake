-- 전월세 종전 보증금·종전 월세를 부호 있는 정수로 (ADR-050). 원천이 종전 보증금에 음수를 보낸다
-- (2022년 일부 시군구의 갱신 계약, 백필 중 관측 — 뜻은 확인하지 못해 원문 그대로 둔다). 처음 만든 표는 UInt32 였다.
-- ch-migrate 가 rent_current.pre_deposit_manwon 이 아직 Nullable(UInt32) 일 때만 한 번 실행한다.
-- 지금 있는 행에는 음수가 없다(파서가 그 파티션을 격리했다) — 형 변환만 하고 값은 그대로.
-- 서빙 표와 스테이징은 구조가 같아야 REPLACE PARTITION 이 되므로 넷을 함께 바꾼다. 수집을 멈춘 상태에서 배포한다.
SET mutations_sync = 2;
ALTER TABLE aptlake.rent_current
    MODIFY COLUMN pre_deposit_manwon Nullable(Int32), MODIFY COLUMN pre_monthly_rent_manwon Nullable(Int32);
ALTER TABLE aptlake.rent_current_staging
    MODIFY COLUMN pre_deposit_manwon Nullable(Int32), MODIFY COLUMN pre_monthly_rent_manwon Nullable(Int32);
ALTER TABLE aptlake.rent_version
    MODIFY COLUMN pre_deposit_manwon Nullable(Int32), MODIFY COLUMN pre_monthly_rent_manwon Nullable(Int32);
ALTER TABLE aptlake.rent_version_staging
    MODIFY COLUMN pre_deposit_manwon Nullable(Int32), MODIFY COLUMN pre_monthly_rent_manwon Nullable(Int32);
