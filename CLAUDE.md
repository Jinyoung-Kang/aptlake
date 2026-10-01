# AptLake — 작업 안내 (Claude Code 용)

전국 아파트 실거래 데이터 레이크하우스. 1인 포트폴리오 프로젝트이며 **공개 저장소**(github.com/Jinyoung-Kang/aptlake)다.
우선순위는 화려한 UI 보다 **아키텍처·성능·보안의 안정성**. 설계 결정은 [docs/decisions.md](docs/decisions.md)(ADR), 운영 절차는 [docs/runbook.md](docs/runbook.md).

## 구조
- `pipeline/` — Dagster + PyIceberg + Trino MERGE(SCD2) + dbt(gold) → ClickHouse 발행. 원천: 국토부 실거래(RTMS), 행안부 법정동코드, R-ONE, V-World
- `api/` — FastAPI. 공개(8610)·내부(8611, 관리 라우트) 앱 분리, 키는 HMAC 저장, 스코프 read/bulk/admin/ops
- `web/` — React 19 + ECharts, nginx BFF(3610)가 서버 쪽 웹 키를 붙인다 (브라우저에 키 없음)
- `infra/` — postgres·minio·lakekeeper·trino·clickhouse·prometheus·grafana 설정, `docker-compose.yml` 17개 서비스
- 계층: bronze(원본, (계약월, 시군구) 파티션) → stage → silver(SCD2) → gold(dbt) → ClickHouse(서빙). 발행마다 데이터셋 버전 `gold@YYYY-MM-DD.n`

## 명령
- 검사: `make lint` (ruff·mypy·tsc), 테스트: `make test-pipeline`, `make test-api`(Testcontainers, Docker 필요)
- 레이크 통합 테스트: `make test-integration` — **실제 운영 레이크·ops DB 를 쓴다**. 수집 실행과 겹치지 않을 때만, 끝나면 합성 파티션(99999/209901) 흔적이 없는지 확인
- 배포:
  - 파이프라인: `make pipeline-redeploy` (진행 중 실행이 끝난 뒤 교체, 센서 자동 재개). `docker compose restart dagster` 로 실행 중 작업을 죽이지 말 것
  - API·웹: `docker compose up -d --build api api-internal web` (ch-migrate·db-migrate 가 먼저 멱등 적용됨)
- 수동 작업은 큐를 거친다: `make month ym=YYYYMM`, `make index`, `make maintenance`
- Trino 직접 질의 (호스트 포트 없음): `docker compose --profile lake exec -T trino trino --user analyst --execute "..."` (`SHOW CREATE TABLE` 등 소유 권한은 `--user pipeline`)
- ClickHouse 관리 질의: 비밀번호는 `.env` 의 `CLICKHOUSE_ADMIN_PASSWORD` 를 셸 변수로만 넘기고 **절대 출력하지 않는다**

## 지켜야 할 규칙
- **추측 값 금지**: 원천 API·공식 표·결정적 조회로 확인되지 않은 값(코드 의미, 분류, 수치)은 화면·문서에 쓰지 않는다. 확인 못 하면 빼고, 그렇다고 말한다 (ADR-018)
- **비밀값**: `.env` 값·API 키를 출력하거나 커밋하지 않는다. 오류 메시지에 키가 든 URL 이 실리지 않게 `http_safe.checked()` 사용
- `.env` 에 외부 키 등록이 필요하면 작업을 멈추고 사용자에게 묻는다
- 커밋: 작성자 이메일은 noreply(`85594012+Jinyoung-Kang@users.noreply.github.com`, 로컬 git 설정됨). 메시지는 한국어, 끝에 `Co-Authored-By` 줄
- 포트폴리오 PDF 에는 GitHub 정보를 넣지 않는다
- 코드 주석·문서는 한국어, 주변 코드의 주석 밀도·말투를 따른다
- 변경하면 해당 ADR·README(테스트 수·측정값)·runbook 도 같이 갱신한다

## 환경 주의
- Docker VM 메모리 7.7GB 를 다른 프로젝트(wakeline)와 함께 쓴다. VM 전체가 모자라면 커널이 Trino 를 죽인다 (ADR-034) — 무거운 작업 전 `docker stats` 로 여유 확인
- 원천 API 는 하루 호출 한도(사용 상한 8,000회)가 있다. bronze 수집을 재시도·재실행하면 예산을 쓴다
- 서비스 포트(모두 127.0.0.1): web 3610, api 8610, api-internal 8611, dagster 3600, grafana 3620, prometheus 9690, minio 9600/9601, clickhouse 8640/9640, postgres 5492, redis 6439
