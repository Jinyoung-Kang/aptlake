-- ops_read: 수집 상태 '보기'만 하는 권한 (QA-001). 웹 BFF 키는 공개 화면이 쓰므로 read + ops_read 만 갖고,
-- 오류 로그 비우기·되돌리기는 ops(운영자 키)만 한다. 웹 키의 권한은 provision 이 배포 때마다 다시 쓴다 (데이터는 여기서 바꾸지 않음).
ALTER TABLE api.api_key DROP CONSTRAINT api_key_scopes_check;
ALTER TABLE api.api_key ADD CONSTRAINT api_key_scopes_check
    CHECK (scopes <@ ARRAY['read','bulk','admin','ops','ops_read']::text[]);
