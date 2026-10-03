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

## 인증키가 거부됐을 때 (수집 상태: "인증키 거부 · 키 확인 필요")
- 포털이 미승인·미등록 키·기간 만료·미등록 IP(사유 코드 20·30·31·32, 또는 사유 없는 401·403)를 주면 파티션을 격리하지 않고 **그날 실행을 멈춘다** (`ops.api_budget.exhausted_reason` 이 `KeyRejected: …`).
- 공공데이터포털 마이페이지에서 키 상태·활용신청(아파트 매매 실거래가 자료)을 확인하고, 새 키면 `.env` 의 `DATA_GO_KR_KEY` 를 바꾼 뒤 `make pipeline-redeploy`. 그날 멈춤은 KST 자정에 풀린다.

## 격리(QUARANTINED) 파티션 재시도
```bash
curl -X POST -H "X-API-Key: $ADMIN" http://127.0.0.1:8611/v1/admin/partitions/41135/2026-08/retry
```
원인은 `/v1/quality/partitions/41135/2026-08` 의 checks·lastError, 원본은 lineage 의 `s3://raw/...` (MinIO 콘솔 http://127.0.0.1:9601).

## 수집 상태 · 오류 로그 보기
- 웹 **수집 상태** 메뉴: 작업 큐(진행·대기 — 실행 중 먼저, 대기는 요청 순 = 큐에서 나갈 순서), 최근 48시간, 스케줄·센서의 다음 실행.
- **로그 비우기**: 확인을 마친 로그는 [로그 비우기]로 숨긴다(원본 기록은 그대로, 감사 로그에 남음). 되돌리기·'비우기 이전 보기'로 다시 볼 수 있다.
  비우기·되돌리기는 **운영자 키(`ops` 권한)**를 확인 줄에 넣어야 한다 — 공개 웹의 키는 보기(`ops_read`)만 한다 (QA-001). 키는 그 화면 상태에만 두고 그 요청에만 붙는다.
- 오류 로그: 기간(24시간·7일·30일)·출처로 거르고, **전체 복사** 또는 **.txt 저장** → 그대로 이슈·메신저에 붙여 넣을 수 있는 형식. 이후 성공으로 복구된 실패는 '해결된 항목 포함'을 켜야 보인다.
- 같은 내용 API: `GET /v1/ops/status`, `GET /v1/ops/errors?hours=168&source=pipeline&includeResolved=true`
  — **`ops` 또는 `ops_read` 스코프 키**가 필요하다 (익명·일반 데이터 키는 403). 비우기·되돌리기(`POST`·`DELETE /v1/ops/errors/clear`)는 `ops` 만.
  운영자 키 발급: 키 발급 요청 본문에 `{"scopes":["read","ops"]}`, 보기만 하는 키는 `["read","ops_read"]`.
  권한을 바꾼 뒤(배포·재발급) Redis 키 캐시가 최대 30초 남아 있을 수 있다.
- Dagster 가 꺼져 있으면(`make serve`) 파이프라인 부분만 '연결 안 됨'으로 나오고 나머지(운영 DB·API 오류)는 그대로 보인다.

## 화면에 오류가 뜰 때 (먼저 API 연결 테스트)
1. 웹 **수집 상태 → API 연결 테스트 → 테스트 실행**. 어느 구성요소가 실패·지연인지, 서빙 DB 메모리가 상한에 가까운지 바로 보인다. [결과 복사]로 공유.
2. `UPSTREAM_UNAVAILABLE`(503) 은 일시 장애 — 화면이 2번 자동 재시도한다. 계속되면 해당 컨테이너 상태 확인:
```bash
docker compose ps
docker stats --no-stream
```
3. `INTERNAL`(500) 은 버그 — 화면에 나온 추적 ID 로 로그를 찾는다:
```bash
docker compose logs api | grep <추적 ID 앞 12자>
```
4. ClickHouse 메모리가 상한 근처면: `docker compose exec clickhouse clickhouse-client -q "SELECT database, table, elapsed, formatReadableSize(memory_usage) FROM system.merges"` 로 무거운 병합이 도는지 본다 (ADR-028).

## 웹 전용 키(BFF) 순환
- `.env` 의 `WEB_API_KEY` 값을 지우고 `make init` → 새 키 생성. `docker compose up -d db-migrate web` → 새 키 등록, 이전 키 폐기(감사 로그 `web_key.ensure`).
- 웹 키는 nginx 컨테이너 환경 변수에만 있고 브라우저·번들에는 없다.
- 웹 키 권한은 `read` + `ops_read` (provision 의 `WEB_SCOPES`). db-migrate 가 돌 때마다 다시 쓰므로 DB 에서 직접 바꾸지 않는다.

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
- 서빙 스키마: `ch-migrate`(API 배포 때 먼저 돈다)가 스키마를 멱등 적용하고, 표 구조를 바꾸는 일회성 이관(`infra/clickhouse/migrate/`, 예: region_month 연 파티션)은 조건이 맞을 때만 실행한다. 이관과 그 표를 쓰는 발행 코드가 함께 바뀌면 **수집을 멈추고**(`dagster sensor stop due_partitions_sensor`, 실행 0 확인) 이관 → `make pipeline-redeploy` 순서로.
  - 발행 코드가 그대로인 이관은 **스테이징을 먼저 지우고** 시작한다(예: trade_current 입도 1024, ADR-038). 그사이 발행은 스테이징이 없거나 교체 직전 행 수 확인(이관이 새로 만든 빈 스테이징)에서 실패하고, 그 달은 `needs_publish` 가 남아 센서가 발행만 다시 한다 — 수집을 멈추지 않아도 서빙에서 발행이 빠지지 않는다. 다만 그 실패가 표 교체 도중(수 ms)에 걸리면 재시도까지 표끼리 잠깐 어긋날 수 있으니, 가능하면 진행 중 실행이 없을 때(`SELECT count(*) FROM runs WHERE status IN ('STARTED','STARTING')` = 0) 배포한다.
  - 이관이 실패하면 ch-migrate 가 스키마를 다시 적용해 스테이징을 되살리고 원래 표는 그대로다. ch-migrate 가 도중에 강제 종료됐다면 스테이징이 없어 발행이 계속 실패하므로 `docker compose up ch-migrate` 를 다시 돌린다.
  - 이관 뒤 확인: `SELECT name, extract(engine_full, 'index_granularity = ([0-9]+)') FROM system.tables WHERE database='aptlake' AND name LIKE 'trade_current%'` 가 둘 다 1024, 행 수·`sum(cityHash64(toString(tuple(*))))` 가 이관 전과 같음, `trade_current_g1024_new` 가 남지 않음.

## 의도한 응답·화면 변경 반영 (골든·스냅숏)
리팩터링은 이 둘이 그대로여야 한다. 응답·화면을 **일부러** 바꿨을 때만 다시 만들고, 차이를 읽은 뒤 같은 커밋에 넣는다.
```bash
cd api && UPDATE_GOLDEN=1 uv run pytest -q tests/test_golden.py   # api/tests/golden/*.json
cd web && npx vitest run -u                                        # src/pages/__snapshots__ (화면은 API 골든 응답으로 그림)
```
- 계층 규칙(`api/tests/test_architecture.py`)이 실패하면 service 가 FastAPI·DB 드라이버를 직접 쓰거나 같은 기능의 repository 를 가져온 것이다 — 저장소는 service 의 Protocol 로 넘긴다.
- 색을 바꾸면 `cd web && npm run check:contrast` 로 테마별 대비표를 본다 (글자 4.5:1, 테두리·초점 3:1 미만이면 `npm run lint` 가 실패).

## Dagster 가 멈춘 것처럼 보일 때
- 컨테이너가 재시작되면 시작 스크립트가 남은 STARTED 실행을 실패 처리하고 풀 슬롯을 반납한다 (로그 `startup:`).
- 수동: `docker compose exec dagster python -m aptlake_pipeline.startup`

## 서빙에 달이 빠졌을 때
- `SELECT * FROM ops.month_state WHERE needs_publish;` 에 있으면 센서가 5분 안에 발행 전용 실행(원천 호출 0회)을 요청한다.
- 수동: `make month ym=202607` 대신 발행만 하려면 Dagster UI 에서 month_pipeline 을 run config `ops.bronze__rtms_raw.config.fetch: false` 로 실행.

## 레이크 직접 질의
Trino·Lakekeeper 는 인증이 없어 호스트 포트를 열지 않는다. 읽기 전용 사용자로 컨테이너 안에서:
```bash
docker compose --profile lake exec trino trino --user analyst --execute "SELECT count(*) FROM lake.silver.apt_trade WHERE is_current"
```

## 유지보수
- 매주 일 04:00 KST `weekly_iceberg_maintenance`: optimize → expire_snapshots(7일) → remove_orphan_files(7일). 7일 안의 시간여행은 항상 가능.

## 백업과 복원
- `make backup` — PostgreSQL 3개 DB(aptlake: 키·클라이언트·감사·수집 상태 / lakekeeper: Iceberg 카탈로그 / dagster: 실행 이력) 덤프,
  ClickHouse `usage_event`(API 사용량·5xx 기록 — 레이크에 없어 다시 만들 수 없음), MinIO `raw`(원천 원본)·`lake`(Iceberg 파일) 증분 복사,
  `.env` 사본을 `backups/` 에 둔다 (git 제외, 권한 700, **비밀값 포함**). `BACKUP_DIR` 로 다른 곳(절대 경로 가능)에.
  - 파이프라인 실행·대기 중이면 거부한다(카탈로그와 파일이 어긋날 수 있음). 확인 질의가 실패해도 거부한다. 주간 레이크 정리(일 04:00) 시간은 피한다.
  - 모든 단계가 끝나야 `pg/<시각>.partial` → `pg/<시각>` 이 된다. `.partial` 이 남아 있으면 실패한 백업이다(지워도 됨).
  - 백업 시점의 핵심 행 수·사용량 파일 행 수·버킷 객체 목록을 `manifest.txt`·`minio-{raw,lake}.txt` 에 적는다.
  - 서빙 표(거래·통계·지수)는 레이크에서 다시 발행하면 되므로 백업하지 않는다.
- `make backup-verify` — 가장 최근의 완료된 백업을 임시 PostgreSQL 컨테이너에 실제로 복원해 **백업 시점 값**과 비교하고(운영 값과 비교하면 백업 뒤 정상 변화에도 실패한다),
  사용량 파일을 다시 읽어 행 수를, MinIO 는 백업 시점에 있던 객체가 사본에 모두 있는지를 이름으로 확인한다.
- 권장: 큰 변경(엔진 업그레이드·스키마 변경) 전, 그리고 주 1회. 오래된 `backups/pg/<시각>` 폴더는 직접 정리한다 (도구는 지우지 않음).
- 복원 (볼륨이 손상됐을 때):
  1. `.env` 를 백업의 `env` 로 되돌린다 (Lakekeeper 암호화 키·DB 비밀번호가 같아야 함).
  2. `docker compose up -d postgres minio` — 새 볼륨이면 `infra/postgres/01-init.sh` 가 역할과 DB(소유자·권한 포함)를 만든다.
     **DB 를 지우고 다시 만들지 말고** 그 DB 에 복원한다 (다시 만들면 소유자·권한이 사라진다):
     `docker compose exec -T postgres pg_restore -U postgres -d <db> --clean --if-exists < backups/pg/<시각>/<db>.dump` (aptlake·lakekeeper·dagster)
  3. MinIO: `docker compose run --rm --no-deps -v "$PWD/backups/minio:/backup" --entrypoint sh minio-init -c 'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" && mc mirror /backup/raw local/raw && mc mirror /backup/lake local/lake'`
  4. `make up` → 서빙 DB 재발행: `docker compose exec -T postgres psql -U postgres -d aptlake -c "UPDATE ops.month_state SET needs_publish = true"` (센서가 원천 호출 없이 발행만 다시 함) + `make index`
  5. 사용량 기록: `docker compose exec -T clickhouse sh -c 'clickhouse-client --user "$CLICKHOUSE_USER" --password "$CLICKHOUSE_PASSWORD" -q "INSERT INTO aptlake.usage_event FORMAT Native"' < backups/pg/<시각>/usage_event.native`

## 서빙 DB·운영 DB 가 느리거나 멈췄을 때
- API 는 ClickHouse 응답을 최대 10초(연결 3초), PostgreSQL 질의 5초·잠금 대기 2초까지만 기다리고 503 + Retry-After 로 끝낸다 (QA-007·008).
  연결 풀이 모두 매달려 있으면 빈 연결을 기다리는 시간이 더해져 한 요청이 15초 안팎 걸릴 수 있다. Redis 는 2초.
- 긴 트랜잭션(수동 정리 작업 등)이 `ops`·`api` 표를 잠그면 그동안 수집 상태·관리 경로가 503 이다 — 데이터 경로는 키 캐시 덕에 영향이 없다.
- 대량 내보내기(exporter 프로필)는 정렬이 150MB 를 넘으면 디스크로 나눠 정렬한다 (`max_bytes_before_external_sort`, 26.8 은 비율 설정을 0 으로 꺼야 적용, QA-006).

## API 문서(/docs) 의 Swagger UI 버전 올리기
- `api/src/aptlake_api/main.py` 의 `SWAGGER_UI` 버전과 `SWAGGER_UI_SRI` 해시를 같이 바꾼다 (해시가 틀리면 브라우저가 스크립트를 막아 화면이 빈다, QA-004):
  `curl -s https://cdn.jsdelivr.net/npm/swagger-ui-dist@<버전>/swagger-ui-bundle.js | openssl dgst -sha384 -binary | openssl base64 -A` (css 도 같게)
- 바꾼 뒤 `/docs` 를 브라우저로 열어 작업 목록이 보이고 콘솔 오류가 없는지 확인한다.

## QA 스택 (운영과 분리된 시험 환경)
- `qa/qa.sh up` — 운영과 볼륨·네트워크(172.30.71.0/24)·포트(+100)·이미지 태그가 겹치지 않는 서빙 계층 (웹 3710·API 8710). `qa/qa.sh destroy` 로 통째로 지운다.
- 시험 데이터: `cd api && uv run python ../qa/seed/seed.py` (운영 규모 생성 데이터·조작 문자열), 시험 키: `qa/keys.sh` → `qa/.env.keys` (git 제외).
- 점검 스크립트: `qa/probes/` (퍼징·값 대조·장애 주입·화면/접근성·Lighthouse·BFF 권한·복원 훈련), 부하: `qa/load/run_k6.sh`. 결과·방법은 `docs/qa/2026-10-qa-report.md`.
- 운영 스택에는 시험 요청을 보내지 않는다. 레이크 통합 시험도 QA 레이크에서: `qa/qa.sh --profile lake up -d --wait lakekeeper lakekeeper-init trino` 뒤 `make test-integration` 과 같은 명령을 `qa/qa.sh --profile lake run ...` 으로.

## 지원 종료(EOL) 점검
- `.github/workflows/eol.yml` 이 매주 endoflife.date 로 이미지의 지원 종료일을 본다 (`uv run --project api python tools/check_eol.py` 로 직접). 조회 실패도 실패로 표시한다.
- GitHub 는 60일 동안 커밋이 없는 공개 저장소의 예약 워크플로를 자동으로 끈다 — 오래 손대지 않았다면 Actions 탭에서 다시 켠다.

## 초기화
`make clean` — 모든 볼륨 삭제 (raw 버킷 Object Lock 도 볼륨과 함께 사라짐).
