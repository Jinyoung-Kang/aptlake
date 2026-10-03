-- 레이크 통합 테스트는 실제 운영 DB 를 쓰고 합성 달(2099xx)을 만든다. 끝나면 그 달의 발행 상태 행까지 지워야
-- 흔적이 남지 않는데, pipeline 역할에는 ops.month_state 삭제 권한이 없다(최소 권한).
-- 표 전체의 DELETE 대신 합성 달만 지울 수 있는 함수 하나를 허용한다.
CREATE FUNCTION ops.forget_synthetic_month(ym text) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, ops AS $$
DECLARE n integer;
BEGIN
    IF ym !~ '^2099(0[1-9]|1[0-2])$' THEN
        RAISE EXCEPTION '합성 달(2099xx)만 지울 수 있습니다: %', ym;
    END IF;
    DELETE FROM ops.month_state WHERE deal_ym = ym;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;
REVOKE ALL ON FUNCTION ops.forget_synthetic_month(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.forget_synthetic_month(text) TO pipeline;
