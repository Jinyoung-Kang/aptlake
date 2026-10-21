-- 단지 차원 토지임대부 여부를 NULL 가능으로 (ADR-018). 전월세에만 있는 단지(매매 기록 없음)는 원천에 이 항목이 없어
-- silver 에서 미상(NULL)인데, 처음 만든 표는 UInt8 이라 발행 경로에서 0(토지임대부 아님)으로 바뀌어 나갔다.
-- ch-migrate 가 complex.land_leasehold 가 아직 UInt8 일 때만 한 번 실행한다. 형 변환만 하고 값(0·1)은 그대로 둔다 —
-- 이관 전에 0 으로 나간 미상은 이관 뒤 첫 차원 발행(dims_and_index)이 표를 통째로 바꾸며 NULL 로 바로잡는다.
-- 단지 차원은 따로 둔 스테이징 표가 없다: 발행마다 complex_next 를 이 표 구조(CREATE TABLE … AS)로 새로 만들어 교체한다.
-- 수집을 멈춘 상태에서 배포한다 (이관 전 구조로 만든 complex_next 와 교체되면 UInt8 로 돌아간다 — 다음 ch-migrate 가 다시 바꾼다).
SET mutations_sync = 2;
ALTER TABLE aptlake.complex MODIFY COLUMN land_leasehold Nullable(UInt8);
