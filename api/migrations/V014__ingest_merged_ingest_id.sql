-- 파티션이 silver 에 반영한 적재본 (ADR-050 보강). 반영 여부를 상태값(MERGED)으로만 판단하면, 반영 전(LOADED) 적재본이
-- 수집 중 되돌리기(RETRY)·예산 소진·수집 실패를 거친 뒤 같은 원본을 다시 받을 때 MERGED 로 넘어가 silver 에 들어가지 않았다.
-- 이제 원본이 같으면 마지막 적재본(last_ingest_id)이 이 값과 같을 때만 MERGED, 다르면 LOADED(다음 반영 대상)로 둔다.
ALTER TABLE ops.ingest_partition ADD COLUMN merged_ingest_id varchar(40);

-- 지금 MERGED 인 파티션은 마지막 적재본이 반영된 것이다. 다른 상태는 반영했는지 기록이 없어 비워 둔다 — 같은 원본을
-- 다시 받으면 한 번 더 반영된다(같은 입력의 MERGE 는 결과가 변하지 않는다 — NFR-01 멱등, 그 달 발행 대기만 켜진다).
UPDATE ops.ingest_partition SET merged_ingest_id = last_ingest_id WHERE status = 'MERGED';

-- 권한: pipeline 은 ops 표 단위 SELECT·INSERT·UPDATE·DELETE(V001)라 새 열도 쓴다. api_app 은 표 단위 SELECT 로 읽기만 —
-- 관리자 재시도의 열 단위 UPDATE(status 등)에는 넣지 않는다.
