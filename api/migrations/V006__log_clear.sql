-- 수집 상태 → 오류 로그 '비우기': 원본 기록(Dagster 실행·품질 검사·사용량)은 지우지 않고, 화면의 기준 시각만 둔다.
-- 이 시각 이전 항목은 기본으로 숨기고 '이전 로그 보기'로 다시 볼 수 있다. 되돌리기 = cleared_at NULL. 변경은 감사 로그에 남긴다.
CREATE TABLE ops.log_view (
    view_id    text PRIMARY KEY CHECK (view_id IN ('errors')),
    cleared_at timestamptz,
    cleared_by varchar(40),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO ops.log_view (view_id) VALUES ('errors');
GRANT SELECT, UPDATE (cleared_at, cleared_by, updated_at) ON ops.log_view TO api_app;
