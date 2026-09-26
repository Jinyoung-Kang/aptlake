-- 시군구 경계 (지도용). 원천: 국토정보플랫폼 V-World LT_C_ADSIGG_INFO (EPSG:4326).
-- 파이프라인이 원본을 raw 버킷에 보관하고, 공식 시군구 목록(ops.region)과 코드·이름이 일치하는 것만 단순화해 저장한다.
CREATE TABLE ops.region_boundary (
    sgg_cd          char(5) PRIMARY KEY,
    geometry        jsonb        NOT NULL,          -- GeoJSON geometry (MultiPolygon), 시각화용 단순화본
    area_rel_error  double precision NOT NULL,      -- 단순화 전후 면적 상대 오차
    source          varchar(60)  NOT NULL,
    source_sha256   char(64)     NOT NULL,          -- 원본 응답 해시 (raw 버킷 키와 같음)
    simplify        jsonb        NOT NULL,          -- 단순화 매개변수 (재현용)
    fetched_at      timestamptz  NOT NULL
);

GRANT SELECT, INSERT, UPDATE, DELETE ON ops.region_boundary TO pipeline;
GRANT SELECT ON ops.region_boundary TO api_app;

-- 웹 화면 전용 플랜: 브라우저에는 키가 없고 웹 프록시(nginx)가 서버 쪽 키를 붙인다.
-- 한도는 키 전체가 아니라 '브라우저 IP 단위'로 적용한다 (aptlake_api.auth).
-- 키 없이 직접 호출하는 anonymous(분당 20회)와 분리해, 화면 탐색이 공개 익명 한도를 쓰지 않게 한다.
INSERT INTO api.plan VALUES ('web', 300, 200000, 60, 200, false);

-- 원천이 먼저 한도 초과를 알린 날의 사유 (예: 'HTTP 429 reason 22'). 수집 상태 화면에 표시
ALTER TABLE ops.api_budget ADD COLUMN exhausted_reason text;
ALTER TABLE ops.api_budget ADD COLUMN exhausted_at timestamptz;
