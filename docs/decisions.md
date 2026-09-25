# 설계 결정 기록 (ADR)

기획서(v0.1)의 ADR 을 이어서, 구현 중 내린 결정과 그 근거를 적는다. "측정/확인"은 실제로 해 본 것, 나머지는 판단이다.

## ADR-008 Spark 대신 Trino MERGE
- 맥락: Docker VM 7.75GB 중 다른 프로젝트가 약 3.8GB. 기획 메모리 예산(Spark 3·Trino 2·ClickHouse 2GB)은 들어가지 않음.
- 결정: silver 반영을 Trino `MERGE INTO`(Iceberg)로. 대량 append(bronze·stage)는 PyIceberg.
- 근거(확인): Trino MERGE 가 조건부 `WHEN MATCHED AND … THEN UPDATE`, `WHEN NOT MATCHED AND … THEN INSERT` 를 지원해 SCD2 를 한 문장으로 쓸 수 있고, 한 문장 = 한 스냅샷.
- 대가: `NOT MATCHED BY SOURCE` 가 없어 원천을 UPSERT·NEWVER·MISSING 세 갈래로 합성해야 함.

## ADR-009 SCD2 는 한 문장 (U-4)
- 두 단계(닫기 → 삽입)면 사이에 실패할 때 현재행이 없는 거래가 생길 수 있고, 재실행 조건을 따로 설계해야 한다.
- 한 문장이면 커밋 단위가 하나이고, 같은 stage 로 재실행하면 모든 행이 '속성 같음'으로 매칭돼 불변 (통합 테스트로 확인).

## ADR-010 카탈로그 = Lakekeeper + vended credentials (U-1)
- Trino·PyIceberg 는 스토리지 키를 설정하지 않는다. Lakekeeper 가 catalog 계정으로 MinIO STS AssumeRole 을 해 테이블 경로 범위 단기 자격증명을 준다.
- 확인: 웨어하우스 생성 시 Lakekeeper 가 저장소 검증에 성공, Trino MERGE·PyIceberg append 모두 이 방식으로 동작.
- 한계: 로컬에서는 카탈로그 인증 없음(allowall) — 도커 네트워크 안 + 호스트 127.0.0.1 만. 운영이면 OIDC.

## ADR-011 Dagster 파티션은 계약월, 시군구 상태는 Postgres 상태 머신
- 256 × 수백 개월의 Dagster 파티션은 실행·이벤트 로그 오버헤드가 수집(호출 1회 ≈ 0.1~0.3초)보다 크다.
- 월 실행 안에서 시군구를 동시 4개·초당 10회로 수집하고, 시군구 단위 상태(PENDING→FETCHING→LOADED→MERGED / RETRY / QUARANTINED)는 `ops.ingest_partition` 에 둔다.
- 시군구 단위 재실행: 관리 API 재시도 → 센서가 그 시군구만 run config 로 요청.

## ADR-012 예산은 Redis Lua + 우선순위 상한 + 센서 계획
- 상한 = 일 한도 × 80%. 증분 몫(시군구×3), 재확인 몫(시군구×12, 대기 중일 때만)을 하위 우선순위가 쓰지 못하게 우선순위별 상한을 둔다.
- 실행 안의 차감은 Lua 로 원자적 (100 스레드 동시 차감 → 정확히 상한, 통합 테스트).
- 센서는 이미 큐에 있는 실행의 예상 호출 수까지 빼고 계획한다 (없던 시절 한 틱에 52개 실행이 쌓이는 문제를 실제로 겪음).

## ADR-013 공개 API 와 관리 API 를 프로세스로 분리
- 기획의 "관리 API 는 127.0.0.1 에서만"을 앱 안에서 검사하면, Docker Desktop 포워딩에서는 모든 요청이 같은 게이트웨이 IP 로 보여 의미가 없다.
- 관리 라우트를 별도 앱(:8611)에만 등록하고, 웹 프록시는 공개 앱만 전달. 공개 앱에는 관리 라우트가 존재하지 않음을 테스트.

## ADR-014 스코프 누락은 기동 실패
- 모든 라우트가 `require_scope(...)` 를 의존성으로 가져야 하고, 기동 시 전 라우트를 검사한다.
- FastAPI 0.141 은 `include_router` 결과를 `_IncludedRouter` 로 감싸 `app.routes` 에서 안쪽 라우트가 보이지 않는다 — 처음 구현은 이 때문에 검사가 사실상 비어 있었고, 테스트로 발견해 안쪽까지 순회하도록 고쳤다.

## ADR-015 발행 = 스테이징 + 대조 + 파티션 교체
- ClickHouse `REPLACE PARTITION … FROM staging` 은 원자적. 교체 전에 Trino 결과와 건수·합계를 비교해 다르면 중단.
- 소형 테이블(시군구·단지·지수)은 `_next` 테이블 적재 후 `EXCHANGE TABLES`.
- 발행이 끝나야 데이터셋 버전이 올라가고, API 결과 캐시 키가 버전을 포함하므로 캐시 무효화가 자동.

## ADR-016 정확 분위수
- `approx_percentile` 은 근사라 "Trino 결과 = ClickHouse 결과" 수용 기준을 설명하기 어렵다. 선형 보간 정확 분위수를 window 함수로 구현(dbt 매크로), numpy 와 차이 0.0 확인.

## ADR-017 지수는 수집이 완결된 달만
- 백필 도중 2024-07 에는 시험 수집한 2개 구만 있었고, 전국 지수 기준월이 그 두 구의 추세를 따라가 2024-08 이 +23% 로 튀는 것을 실제로 봤다.
- 규칙: 시군구 90% 이상 MERGED 인 계약월만 지수 입력. 제외된 달은 실행 메타데이터에 남긴다.

## ADR-018 추측 값 금지
- 시군구 코드·이름은 행안부 법정동코드 API, 지수 기준값은 R-ONE 통계표에서 가져온다.
- 약칭 → 시도 코드처럼 표가 필요해 보이는 곳도 공식 명칭에 대한 결정적 규칙(유일할 때만)으로 매핑하고, 모호하면 매핑하지 않는다.
- 상위 시(일반구를 둔 시) 판별은 locathigh_cd 가 도를 가리켜 쓸 수 없음을 확인하고, 공식 명칭 포함 관계를 쓴다.

## ADR-019 발행 대기 상태를 따로 추적 (실패 복구)
- 겪은 문제: 실행이 silver 반영 뒤·ClickHouse 발행 전에 죽으면(컨테이너 재시작), 그 달의 원천 파티션은 이미 MERGED 이고
  다음 수집은 원본 해시가 같아 bronze·silver 가 산출물을 내지 않는다 → gold·발행이 다시는 실행되지 않아 **서빙에서 영구 누락**
  (실제로 2026-06·07 이 silver 에는 있고 ClickHouse 에는 없었다).
- 결정: `ops.month_state.needs_publish` — silver 반영 성공 시 true, 발행 성공 시 false.
  bronze 에 `fetch=False`(원천 호출 없음) 모드를 두고, 센서가 발행 대기 달에 호출 0회 실행을 요청한다.
  이때 silver 는 반영 없이도 현재 레이크 상태(현재행 vs 마지막 stage)로 blocking 검사를 **다시 계산**해 통과해야 하위로 넘긴다.
- 발행은 스테이징 대조 + 파티션 교체라 같은 달을 여러 번 발행해도 결과가 같다 (V003 마이그레이션이 기존 반영 달을 전부 한 번 재발행하도록 표시).

## ADR-020 원천 한도 초과는 '격리'가 아니라 '오늘은 중단'
- 겪은 문제: 포털이 일일 한도 초과를 **HTTP 429** 로 돌려주는데, 클라이언트가 4xx 를 영구 오류로 보고 시군구 파티션을 즉시 격리했다.
  한 달 전체(256개)가 격리돼 blocking 검사(격리율 < 20%)가 하위 반영을 막았고, 1,290개 파티션이 잘못 격리됐다.
  (검사 자체는 의도대로 동작해 잘못된 데이터가 퍼지지 않았다 — 분류가 틀렸다.)
- 원인: 내부 카운터는 7,170회였는데 포털은 한도 초과. 한도는 인증키 단위라 같은 키를 쓰는 다른 프로그램의 호출도 합산된다.
- 결정: HTTP 429 또는 포털 오류 봉투의 사유 코드 22 → `QuotaExceeded` (실행 중단, attempts 되돌림).
  그날 예산을 상한까지 채워 `exhausted_by_source` 사유와 함께 기록 → 센서는 KST 자정까지 수집 실행을 만들지 않는다.
  잘못 격리된 파티션은 사유를 남기고 RETRY 로 되돌렸다. 키 오류(30 등)는 여전히 즉시 실패.

## ADR-021 Trino 메모리: 힙이 아니라 프로세스 전체(RSS)로 한도를 맞춘다
- 겪은 문제: 단지 차원 전체 재생성(dbt `complex_summary`) 중 Trino 가 컨테이너 한도 2GB 에서 OOM-kill.
  힙 1.4GB + 코드 캐시·다이렉트 버퍼·메타스페이스·스레드 스택이 합쳐 한도를 넘었다.
- 결정: 힙 1.2GB, `MaxDirectMemorySize=256M`, `MaxMetaspaceSize=256M`, 질의 메모리 600MB. 같은 작업에서 최대 1.8GiB 로 측정.
- 함께: 상주 서비스 전부 `restart: unless-stopped` (그 전에는 죽으면 그대로 멈춰 있었다).
  수동 실행(`make month·index·maintenance`)은 `job launch` 로 큐를 거친다 — 센서 실행과 한 컨테이너 메모리를 동시에 쓰다
  지수 산출 프로세스가 SIGKILL 된 적이 있다. 지수 입력은 문자열을 사전 인코딩 정수로만 다뤄 최대 RSS 980MB.
