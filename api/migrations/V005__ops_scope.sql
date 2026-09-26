-- 운영 정보(작업 큐·오류 로그·구성요소 점검)는 'ops' 스코프가 있는 키만.
-- 오류 로그에는 (비밀값을 가려도) 내부 호스트 이름·스택 경로가 남으므로 익명·일반 데이터 키에는 주지 않는다 (최소 권한).
-- 웹 화면(BFF) 키와 운영자가 명시적으로 발급한 키만 가진다.
ALTER TABLE api.api_key DROP CONSTRAINT api_key_scopes_check;
ALTER TABLE api.api_key ADD CONSTRAINT api_key_scopes_check
    CHECK (scopes <@ ARRAY['read','bulk','admin','ops']::text[]);

UPDATE api.api_key k SET scopes = array_append(k.scopes, 'ops')
  FROM api.client c
 WHERE c.client_id = k.client_id AND c.name = 'web-ui' AND NOT ('ops' = ANY (k.scopes));
