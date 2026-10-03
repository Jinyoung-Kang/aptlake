# AptLake — 로컬 운영 명령 모음 (macOS · Apple Silicon · Docker Desktop)
SHELL := /bin/bash
COMPOSE := docker compose
LAKE := $(COMPOSE) --profile lake
ALL := $(COMPOSE) --profile lake --profile obs
DAGSTER := $(LAKE) exec -T dagster

.PHONY: help init up serve obs down backup backup-verify clean ps logs admin-key demo-keys loadtest-key pipeline-redeploy lake-init regions backfill-start backfill-stop \
        month index maintenance test test-api test-pipeline test-web test-integration lint loadtest web-dev audit

help: ## 명령 목록
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

init: ## .env 생성 + 내부 비밀값 자동 생성 (외부 API 키는 직접 입력)
	@python3 tools/init_env.py

up: init ## 전체 스택 (서빙 + 레이크 + Dagster)
	$(LAKE) up -d --build
	@echo "web http://127.0.0.1:3610 · api http://127.0.0.1:8610/docs · dagster http://127.0.0.1:3600"

serve: init ## 서빙 계층만 (ClickHouse·API·웹) — 수집 없이 조회만 할 때, 메모리 절약
	$(COMPOSE) up -d --build

obs: ## Prometheus·Grafana 추가 기동 (grafana http://127.0.0.1:3620, admin / .env 의 GRAFANA_ADMIN_PASSWORD)
	$(ALL) up -d

down: ## 중지 (데이터 보존)
	$(ALL) down

backup: ## 백업: PostgreSQL 3개 DB 덤프 + MinIO 원본·레이크 증분 복사 + .env (backups/, git 제외·권한 700)
	@tools/backup.sh

backup-verify: ## 최근 백업을 임시 PostgreSQL 에 실제로 복원해 운영과 비교 (운영 데이터는 건드리지 않음)
	@tools/backup_verify.sh

clean: ## 중지 + 모든 볼륨 삭제 (데이터 초기화!)
	$(ALL) down -v

ps: ## 상태
	$(ALL) ps

logs: ## 로그 (예: make logs s=dagster)
	$(ALL) logs -f --tail 200 $(s)

admin-key: ## 관리자 키 발급 (1회 표시)
	@$(COMPOSE) exec -T api-internal python -m aptlake_api.cli bootstrap-admin

demo-keys: ## free·pro 데모 키 발급 (1회 표시)
	@$(COMPOSE) exec -T api-internal python -m aptlake_api.cli demo-keys

loadtest-key: ## 부하 측정용 키 (loadtest 플랜, 1일 만료)
	@$(COMPOSE) exec -T api-internal python -m aptlake_api.cli loadtest-key

lake-init: ## Iceberg 테이블 생성 + 시군구 목록 적재 (처음 한 번, 완료까지 대기)
	$(DAGSTER) dagster job execute -m aptlake_pipeline.definitions -j refresh_regions

regions: lake-init ## (별칭) 시군구 목록 갱신

backfill-start: ## 수집 센서 켜기 (예산 안에서 증분 > 재시도 > 재확인 > 백필 순으로 자동 실행)
	$(DAGSTER) dagster sensor start due_partitions_sensor -m aptlake_pipeline.definitions
	$(DAGSTER) dagster schedule start daily_dims_and_index -m aptlake_pipeline.definitions
	$(DAGSTER) dagster schedule start weekly_iceberg_maintenance -m aptlake_pipeline.definitions
	$(DAGSTER) dagster schedule start monthly_regions -m aptlake_pipeline.definitions

backfill-stop: ## 수집 센서·스케줄 끄기 (원천 API 를 부르지 않는 주간 Iceberg 정리는 계속 둔다)
	-$(DAGSTER) dagster sensor stop due_partitions_sensor -m aptlake_pipeline.definitions
	-$(DAGSTER) dagster schedule stop daily_dims_and_index -m aptlake_pipeline.definitions
	-$(DAGSTER) dagster schedule stop monthly_regions -m aptlake_pipeline.definitions

pipeline-redeploy: ## 파이프라인 코드 무중단 교체: 센서 멈춤 → 대기 실행 취소 → 진행 중 실행 완료 대기 → 재빌드 → 센서 재개
	-$(DAGSTER) dagster sensor stop due_partitions_sensor -m aptlake_pipeline.definitions
	$(DAGSTER) python -c "from dagster import DagsterInstance as I, RunsFilter as F, DagsterRunStatus as S; i=I.get(); [i.report_run_canceled(r, message='redeploy') for r in i.get_runs(filters=F(statuses=[S.QUEUED]))]"
	@until [ "$$($(COMPOSE) exec -T postgres psql -U postgres -d dagster -Atc "SELECT count(*) FROM runs WHERE status IN ('STARTED','STARTING')")" = "0" ]; do echo "진행 중 실행 완료 대기…"; sleep 15; done
	$(LAKE) up -d --build dagster
	@until $(LAKE) logs dagster --since 2m 2>&1 | grep -q "Serving dagster-webserver"; do sleep 3; done
	$(DAGSTER) dagster sensor start due_partitions_sensor -m aptlake_pipeline.definitions

# 아래 수동 실행은 큐를 거친다 (동시 실행 1개 — 센서가 띄운 실행과 한 컨테이너 메모리를 나눠 쓰지 않도록). 진행은 Dagster UI
month: ## 한 달 파티션 실행 요청 (예: make month ym=202408)
	$(DAGSTER) dagster job launch -m aptlake_pipeline.definitions -j month_pipeline --tags '{"dagster/partition": "$(ym)"}'

index: ## 단지 차원·자체 지수·R-ONE 검증 산출 + 발행 요청
	$(DAGSTER) dagster job launch -m aptlake_pipeline.definitions -j dims_and_index

maintenance: ## Iceberg 압축·스냅샷 만료·고아 파일 정리 요청
	$(DAGSTER) dagster job launch -m aptlake_pipeline.definitions -j iceberg_maintenance

test: test-pipeline test-api test-web ## 단위 + API + 웹 테스트 (Testcontainers 사용: Docker 필요)

test-pipeline: ## 파이프라인 단위 테스트 (파서·지문·지수·예산·센서 계획)
	cd pipeline/dbt && uv run dbt parse --profiles-dir . --quiet  # 매번 (약 2초) — 남아 있던 옛 manifest 로 시험하지 않게
	cd pipeline && uv run pytest -q

test-api: ## API 테스트 (Testcontainers 로 PostgreSQL·Redis·ClickHouse 임시 기동)
	cd api && uv run pytest -q

test-web: ## 웹 테스트 (순수 함수·API 클라이언트·화면 특성 스냅숏, Vitest)
	cd web && npm test

test-integration: ## 정합성 통합 테스트 (실행 중인 레이크 스택에서 SCD2 MERGE 검증)
	$(LAKE) run --rm --no-deps -T --user root -v "$$PWD/pipeline/tests:/opt/aptlake/tests:ro" -w /opt/aptlake dagster \
	  sh -c "uv pip install -q --python /opt/venv/bin/python pytest hypothesis && python -m pytest -q -p no:cacheprovider -m integration tests/test_integration_silver.py"

lint: ## ruff · mypy · tsc · Biome(웹 린트) · 색 대비(WCAG)
	cd pipeline && uv run ruff check src tests && uv run ruff format --check src tests
	cd api && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src
	cd web && npx tsc -b && npx tsc -p tsconfig.test.json && npm run lint

audit: ## 의존성 취약점 검사 (pip-audit · npm audit)
	cd pipeline && uv export --frozen --no-dev --no-hashes -o /tmp/aptlake-pipeline-req.txt >/dev/null && uv run pip-audit -r /tmp/aptlake-pipeline-req.txt
	cd api && uv export --frozen --no-dev --no-hashes -o /tmp/aptlake-api-req.txt >/dev/null && uv run pip-audit -r /tmp/aptlake-api-req.txt
	cd web && npm audit --omit=dev

loadtest: ## k6 부하 측정 — 도커 네트워크 안에서 api:8610 직접 (수집 센서는 잠시 끄고 재는 것을 권장)
	@test -n "$$APTLAKE_KEY" || (echo "APTLAKE_KEY 필요: export APTLAKE_KEY=\$$(make -s loadtest-key)" && exit 1)
	mkdir -p loadtest/results
	docker run --rm -i --network aptlake_default -e APTLAKE_KEY -e BASE=$${BASE:-http://api:8610} -e RATE_MONTHS -e RATE_TRADES -e DURATION \
	  -v "$$PWD/loadtest:/scripts" grafana/k6:1.3.0 run --summary-export=/scripts/results/summary.json /scripts/api.js

web-dev: ## 웹 개발 서버 (http://127.0.0.1:3611, API 프록시)
	cd web && npm run dev
