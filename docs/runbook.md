# 운영 절차

## 처음 기동
1. `make init` → `.env` 의 `DATA_GO_KR_KEY`(공공데이터포털 Decoding 키: 아파트 매매 실거래가 자료 + 행정안전부_법정동코드 활용신청), `REB_API_KEY`(R-ONE) 입력
2. `make up` → `make lake-init` → `make backfill-start`
3. `make admin-key` 로 관리자 키 발급 (30일 만료, 1회 표시)

## 백필 범위·속도
- `.env` 의 `BACKFILL_FROM` (기본 2021-01, 원천은 2006-01 부터 제공). 바꾸면 `make pipeline-redeploy`.
- 하루 사용 상한 = `RTMS_DAILY_LIMIT × RTMS_BUDGET_PCT%` (기본 8,000회 ≈ 31개월치). 개발계정 한도를 올리면 `RTMS_DAILY_LIMIT` 만 바꾸면 된다.
- 진행 상황: 웹 "데이터 품질" 탭 히트맵, Dagster Runs, `/v1/quality/summary`.

## 원천 한도 초과가 났을 때
- 포털이 `HTTP 429 / returnReasonCode 22` 를 주면 그날 예산이 소진 처리되고(`ops.api_budget`), 센서는 KST 자정 뒤 자동으로 이어간다.
- 같은 인증키를 다른 프로그램(예: 다른 프로젝트의 실거래 수집)과 함께 쓰면 한도가 합산된다 → 이 서비스 전용 키를 발급받거나 `RTMS_BUDGET_PCT` 를 낮춘다.

## 격리(QUARANTINED) 파티션 재시도
```bash
curl -X POST -H "X-API-Key: $ADMIN" http://127.0.0.1:8611/v1/admin/partitions/41135/2026-08/retry
```
원인은 `/v1/quality/partitions/41135/2026-08` 의 checks·lastError, 원본은 lineage 의 `s3://raw/...` (MinIO 콘솔 http://127.0.0.1:9601).

## 수집 상태 · 오류 로그 보기
- 웹 **수집 상태** 메뉴: 작업 큐(진행·대기 — 실행 중 먼저, 대기는 요청 순 = 큐에서 나갈 순서), 최근 48시간, 스케줄·센서의 다음 실행.
- 오류 로그: 기간(24시간·7일·30일)·출처로 거르고, **전체 복사** 또는 **.txt 저장** → 그대로 이슈·메신저에 붙여 넣을 수 있는 형식. 이후 성공으로 복구된 실패는 '해결된 항목 포함'을 켜야 보인다.
- 같은 내용 API: `GET /v1/ops/status`, `GET /v1/ops/errors?hours=168&source=pipeline&includeResolved=true`
- Dagster 가 꺼져 있으면(`make serve`) 파이프라인 부분만 '연결 안 됨'으로 나오고 나머지(운영 DB·API 오류)는 그대로 보인다.

## 웹 전용 키(BFF) 순환
- `.env` 의 `WEB_API_KEY` 값을 지우고 `make init` → 새 키 생성. `docker compose up -d db-migrate web` → 새 키 등록, 이전 키 폐기(감사 로그 `web_key.ensure`).
- 웹 키는 nginx 컨테이너 환경 변수에만 있고 브라우저·번들에는 없다.

## 지도 경계 갱신
- 매월 `monthly_regions` 스케줄이 시군구 목록과 함께 다시 받는다. 수동: Dagster 에서 `refresh_regions` 작업 실행.
- 검사 `boundary_matches_official_regions` 가 경고면 공식 목록과 코드·이름이 다른 시군구가 있다는 뜻 — 그 시군구는 지도에서 회색(자료 없음)으로 남고, 추측으로 채우지 않는다.

## 키 관리
```bash
# 클라이언트 생성 → 키 발급 (응답의 apiKey 는 다시 볼 수 없음)
curl -X POST -H "X-API-Key: $ADMIN" -H 'content-type: application/json' \
  -d '{"name":"partner-a","planId":"free"}' http://127.0.0.1:8611/v1/admin/clients
curl -X POST -H "X-API-Key: $ADMIN" -H 'content-type: application/json' \
  -d '{"scopes":["read"],"expiresInDays":90}' http://127.0.0.1:8611/v1/admin/clients/<clientId>/keys
# 폐기 (즉시 401)
curl -X DELETE -H "X-API-Key: $ADMIN" http://127.0.0.1:8611/v1/admin/keys/<keyId>
```
감사 로그: `SELECT * FROM api.audit_log ORDER BY at DESC;`

## 코드 교체
- 파이프라인: `make pipeline-redeploy` (진행 중인 달을 끝낸 뒤 교체 — 중간에 죽이면 그 달은 FETCHING 으로 남았다가 1시간 뒤 다시 수집됨)
- API·웹: `docker compose up -d --build api api-internal web` (웹 nginx 는 API 컨테이너 IP 변경을 10초 안에 따라감)

## Dagster 가 멈춘 것처럼 보일 때
- 컨테이너가 재시작되면 시작 스크립트가 남은 STARTED 실행을 실패 처리하고 풀 슬롯을 반납한다 (로그 `startup:`).
- 수동: `docker compose exec dagster python -m aptlake_pipeline.startup`

## 서빙에 달이 빠졌을 때
- `SELECT * FROM ops.month_state WHERE needs_publish;` 에 있으면 센서가 5분 안에 발행 전용 실행(원천 호출 0회)을 요청한다.
- 수동: `make month ym=202607` 대신 발행만 하려면 Dagster UI 에서 month_pipeline 을 run config `ops.bronze__rtms_raw.config.fetch: false` 로 실행.

## 유지보수
- 매주 일 04:00 KST `weekly_iceberg_maintenance`: optimize → expire_snapshots(7일) → remove_orphan_files(7일). 7일 안의 시간여행은 항상 가능.

## 초기화
`make clean` — 모든 볼륨 삭제 (raw 버킷 Object Lock 도 볼륨과 함께 사라짐).
