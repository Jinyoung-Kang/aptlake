-- 공개 플랜 표시 (ADR-047): 개발자 화면·GET /v1/plans 가 보여 줄 플랜. 웹(BFF)·부하 측정용 플랜은 내부용이라 숨긴다.
-- 화면에 숫자를 코드로 박아 두지 않고 이 표에서 읽는다 (값의 출처를 하나로).
ALTER TABLE api.plan ADD COLUMN public boolean NOT NULL DEFAULT false;
UPDATE api.plan SET public = true WHERE plan_id IN ('anonymous', 'free', 'pro');
