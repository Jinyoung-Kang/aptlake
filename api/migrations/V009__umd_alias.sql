-- 법정동 이름 대응표: 행정구역 승격·개칭으로 원천의 법정동 이름이 바뀌어도 같은 거래가 같은 지문을 갖게 한다.
-- 겪은 일: 대구 달성군 구지면 → 구지읍. 원천이 그 지역 거래의 법정동 이름을 빈 값으로 보내다가 새 이름으로 채우면
-- 지문 키(법정동 이름 포함)가 바뀌어 같은 거래가 새 거래로 들어간다.
-- 파이프라인이 행정안전부 법정동코드(현재 목록)와 관측한 이름으로 만든다(면→읍 승격, regions.derive_umd_aliases).
-- 더하기만 한다 — 지우거나 바꾸면 그 이름의 거래 지문이 다시 바뀐다. 그래서 pipeline 역할에 UPDATE·DELETE 를 주지 않는다.
CREATE TABLE ops.umd_alias (
    sgg_cd      char(5)      NOT NULL,
    new_name    varchar(60)  NOT NULL,   -- 지금 공식 이름 (원천이 보내는 새 이름)
    key_name    varchar(60)  NOT NULL,   -- 지문·단지 키에 쓰는 옛 이름 (처음 관측한 이름)
    rule        varchar(40)  NOT NULL,
    source      varchar(60)  NOT NULL,
    derived_at  timestamptz  NOT NULL,
    PRIMARY KEY (sgg_cd, new_name)
);

GRANT SELECT, INSERT ON ops.umd_alias TO pipeline;
GRANT SELECT ON ops.umd_alias TO api_app;
