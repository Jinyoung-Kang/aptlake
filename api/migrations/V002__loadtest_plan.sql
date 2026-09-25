-- 부하 측정 전용 플랜. 관리 API(POST /admin/clients)는 free·pro 만 허용하므로 CLI 로만 발급된다.
INSERT INTO api.plan VALUES ('loadtest', 1000000, 100000000, NULL, 1000, false);
