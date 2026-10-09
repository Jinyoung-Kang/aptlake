# 데이터 제품 설계 계획서 — 변경분 피드 · 버전 스냅숏 내보내기 · 원천 반영 지연 · 플랜과 사용량

상태: **구현됨** (PR #33~#37, ADR-043~047) · 작성 기준 커밋 `4d45c86`

> 구현하며 바뀐 점: ① 기록 시작을 기준선 버전(`snapshot_baseline`)으로 명시 — 기준선 전에 끼어든 달 발행으로 불완전한 버전을 내보내지 않게. ② 피드 읽기는 창 전체 정렬 대신 발행 단위로 기본 키를 타게(90일 창 p95 2,085ms → 33ms). ③ L3 기록은 V011 따로, 플랜 공개는 V012. ④ D2 는 이력 표에 열을 더하는 대신 `untrackedSourceFields` 로 알림(70개월 재발행이 필요해서). 자세한 이유는 각 ADR. · 짝 문서: [기능 명세서](2026-10-data-product-spec.md)

## 0. 설계를 묶는 지금 구조의 사실 (코드로 확인)

| 사실 | 근거 | 설계에 주는 영향 |
|---|---|---|
| ClickHouse 서빙은 **최신만** 들고 있다. 발행은 달 단위로 스테이징 → 대조 → `REPLACE PARTITION` | `publish.py:148-163` | 과거 버전은 서빙에서 다시 만들 수 없다 |
| Iceberg 스냅숏은 **7일**만 남는다 (주 1회 정리라 실제로는 7~14일) | `maintenance.py:34,108` | 레이크 시간 여행으로는 F3 의 오래된 버전을 못 만든다 |
| silver `valid_from` 은 발행 시각이 아니라 **수집 시각**이다. 병합이 늦게 되거나 다시 시도돼도 그대로다 | `silver.py:94,129`, `ingest.py:204` | `valid_from > 발행 시각` 같은 조건으로는 "그 버전에 보이던 것"을 정확히 못 고른다 |
| 차단 검사가 실패해도 silver MERGE 는 이미 커밋돼 있다 (발행만 막힘) | `silver.py:270-290` | silver 를 기준으로 피드를 만들면 서빙에 안 나간 변경이 섞인다 |
| 발행 기록 `ops.dataset_version` 에 버전·시각·달·Iceberg 스냅숏 ID 가 있지만, 행 단위 변경은 없다 | `V001:66-75`, `publish.py:422-440` | 변경 기록과 달별 상태를 새로 남겨야 한다 |
| 버전 문자열은 발행 **전에** 정하고(`next_dataset_version`), 실패 후 재시도하면 같은 문자열이 다시 나온다 | `definitions.py:408-414`, `publish.py:411-419` | 기록 대상마다 버전 문자열 대신 **발행 ID(uuid)** 를 붙여야 한다 |
| 실행은 한 번에 하나, 발행 풀도 1 | `dagster_home/dagster.yaml:19-23` | 발행 사이 경쟁은 없다 — 순서만 신경 쓰면 된다 |
| 내보내기는 내부 앱(api-internal) 안의 작업자가 ClickHouse `trade_current` 를 읽어 Parquet 으로 쓰고, 결과는 1일 뒤 지워진다 | `exports/worker.py`, `storage.py:65-105`, `minio/init.sh:21` | F3 은 이 작업자를 확장한다 |
| 사용량 `usage_event` 는 요청마다 한 줄(고객·키·경로·상태·차감 행), 180일 보관. 하루 행 한도는 **키(주체)** 기준, `/me/usage` 는 **고객** 기준 | `01-schema.sql:142-157`, `core/auth.py:231-258`, `usage/repository.py` | F5 화면에서 기준이 다른 숫자를 나눠 보여야 한다 |
| 웹 개발자 화면의 플랜 표는 코드에 박혀 있다 | `DeveloperPage.tsx:73-83` | F5 에서 API 응답으로 바꾼다 |
| 내부 비밀값(`*_SECRET` 등)은 `make init` 이 `.env.example` 의 새 이름을 보고 자동 생성한다 | `tools/init_env.py:30-43` | 새 MinIO 계정 비밀은 사용자가 직접 넣을 필요가 없다 (외부 키 아님) |

## 1. 공통 기반 — 발행 기록과 달별 스냅숏 파일

F2·F3 을 한 장치로 받친다: **달을 발행할 때마다 그 달의 서빙 내용(`trade_current` 스테이징)을 Parquet 파일로 남기고, 버전 기록과 같은 트랜잭션으로 등록한다.**

```
clickhouse_month(ym)
  1. publish_id = uuid4(), version = next_dataset_version()            (지금과 같음 + 발행 ID)
  2. 네 표 스테이징 → 대조                                              (지금과 같음)
  3. [신규] 스테이징 trade_current(그 달) → Parquet → MinIO snapshots/trade/deal_ym=YYYYMM/{publish_id}.parquet
            행 수 = 대조 값 확인, sha256·바이트 기록
  4. [신규·F2] 직전 '기록된' 달 파일과 비교 → 변경 행을 ClickHouse trade_change 에 (publish_id 붙여) 적재
  5. 네 표 REPLACE PARTITION                                           (지금과 같음)
  6. [변경] PostgreSQL 한 트랜잭션: dataset_version(+publish_id) + dataset_month_file + publish_lag(F4)
  7. Redis al:ds:ver 갱신                                               (지금과 같음)
```

- **보이는 조건:** API 는 6이 커밋된 `publish_id` 의 파일·변경만 쓴다. 3·4 가 남긴 고아 파일·행은 보이지 않고 정리 작업이 지운다.
- **잃지 않는 조건:** 4의 비교 기준은 "서빙의 지금 값"이 아니라 **"마지막으로 기록된 그 달 파일"**이다.
  - 5 뒤 6만 실패해도, 다음 발행이 같은 기준과 비교한다. 그래서 그 변경이 다음 발행에 빠짐없이 들어간다.
  - 서빙과 비교했다면 이 경우 변경이 사라진다.
- **기준선(baseline):** 배포 직후 새 작업 `snapshot_baseline` 이 지금 서빙의 70개월을 달 파일로 쓰고, 버전 하나로 기록한다(서빙 데이터는 그대로). 이 버전이 F2·F3 의 시작점이다. 그 전 버전은 410 으로 답한다.
- **파일 쓰기 주체:** 파이프라인(Dagster)이 Python 으로 쓴다(ClickHouse 화살표 스트림 → pyarrow → S3).
  - ClickHouse `s3()` 함수로 쓰면 질의 문자열에 자격증명이 들어가 `query_log` 에 남을 수 있어서 쓰지 않는다.
- **새 MinIO 계정 `snapshot`:** `snapshots/*` 에 Put·Get·Delete, 버킷 List 만. 기존 `export` 계정에는 `snapshots/*` Get 만 더한다.
- **보관 정리:** 주간 Iceberg 정리 작업에 함께 넣는다.
  - 보관 정책(명세 7장 결정 3) 밖의 버전이 참조하는 파일 중, 다른 보관 버전이 참조하지 않는 파일을 지운다.
  - `dataset_month_file.deleted_at` 을 채운다. 발행 기록 행 자체는 지우지 않는다(감사).

### 1.1 버전 V 의 파일 목록 (순수 함수)
- 각 달마다, 기록된 발행 중 `published_at ≤ V.published_at` 인 가장 최근 달 파일 하나.
- 차원·지수 발행(`partitions = []`)은 파일이 없어 직전 파일을 그대로 쓴다.
- 고르는 규칙은 `features/exports/service.py`(API)와 파이프라인 정리 작업이 같이 쓴다. 그래서 같은 규칙을 양쪽에서 시험한다(같은 시험 사례 표).

## 2. F3 버전 스냅숏 내보내기

| 부분 | 변경 |
|---|---|
| `POST /v1/exports` | `datasetVersion` 선택값. 생략하면 요청 시점 최신 버전을 **저장**한다(작업이 늦게 돌아도 요청 시점 버전) |
| 검증 (service, 순수) | 버전 존재 → 404 `VERSION_NOT_FOUND`. 기준선 이전이거나 참조 파일이 지워졌으면 → 410 `SNAPSHOT_EXPIRED`(이용 가능한 가장 오래된 버전 포함) |
| 작업자 | 1.1 의 파일 목록 중 `from`~`to` 에 걸치는 달만 차례로 읽는다 → pyarrow 필터(`sgg_cd`·날짜·해제) → 같은 21개 열로 Parquet 출력. 달 파일이 (`deal_date`, `trade_id`) 순으로 정렬돼 있으니 달 순서대로 이어 쓰면 전체 순서가 유지된다 |
| 메모리 | 달 하나씩 처리(내부 앱 512 MB 상한). QA 의 내보내기 메모리 시험(`test_qa_export_memory.py`)을 새 경로로 다시 돌린다 |
| `GET /v1/exports/{id}` | `datasetVersion`·`sha256`·`expiresAt` 추가. `finished_at + 1일` 이 지나면 `expired` 로 답하고 주소를 만들지 않는다(D3, 상태는 계산으로 — DB 상태값은 늘리지 않음) |
| 경로 하나로 (결정 5) | 최신 버전도 파일에서 읽는다. 검증 뒤 `exporter` ClickHouse 사용자의 `trade_current` 권한을 뺀다(권한 축소) |

- **기각한 대안**
  - Iceberg 시간 여행: 7~14일만 된다. API 에 Trino 의존이 생기고, 레이크 프로필이 꺼져 있으면 안 된다. 또 발행에 실패한 달의 gold 가 섞이는 경우가 있다.
  - silver `valid_from` 으로 재구성: 0장의 이유로 정확하지 않다.
  - 버전마다 전체 파일: 하루에도 여러 번 발행해서 용량이 크다. 달 파일 재사용으로 바뀐 달만 쓴다.

## 3. F2 변경분 피드

### 3.1 저장
ClickHouse `aptlake.trade_change` (신규):
```sql
CREATE TABLE IF NOT EXISTS aptlake.trade_change (
    publish_id     UUID,
    deal_ym        UInt32,
    trade_id       String,
    change         Enum8('added' = 1, 'removed' = 2, 'updated' = 3, 'missing' = 4, 'restored' = 5),
    changed_fields Array(LowCardinality(String)),
    -- trade_current 와 같은 열 (removed 면 직전 값) …
    created_at     DateTime64(3, 'UTC') DEFAULT now64(3)
) ENGINE = MergeTree PARTITION BY toYYYYMM(created_at) ORDER BY (publish_id, trade_id)
  TTL toDateTime(created_at) + INTERVAL 90 DAY DELETE;
```
- 행에 그때의 거래 값을 함께 넣는다. 나중에 `trade_current` 가 또 바뀌어도 피드는 그 발행 시점 값을 준다.
- 보존 일수는 API 설정과 같아야 한다. 스키마 파일과 설정 값이 같은지 시험한다(한쪽만 바꾸면 실패).

### 3.2 비교 (파이프라인, 순수 함수)
- `diff_month(old_rows, new_rows) -> list[Change]` 를 `pipeline/src/aptlake_pipeline/changes.py` 에 둔다.
  - 한 달 행 수 규모라 Python 메모리로 충분하다. 행 수는 실측해 성능 문서에 남긴다.
- 분류 규칙 (명세 2.4):
  - 거래 ID 가 한쪽에만 있다 → `added` / `removed`
  - 서빙 열이 다르다 → `updated`
    - `changed_fields` 는 응답 이름이다. `version`·`isOutlier`·`missingSince` 도 포함한다.
    - D2 처럼 서빙하지 않는 열만 바뀌어 `version` 만 다르면, `changed_fields = ["version"]` 그대로 둔다(숨기지 않음).
  - 다른 열은 같고 `missing_since` 만 비었다↔찼다 → `missing` / `restored`
- 기준 파일이 없는 달(기준선 이후 처음 생긴 계약월)은 모두 `added`.

### 3.3 API — `features/changes/` (router·service·repository, 구조 시험 규칙대로)
- **service (순수)**
  - 버전 창 계산: `since` 다음부터 `until` 까지, 기록된 발행만 → 404 / 410 / 빈 결과 판정
  - 커서: 위치 = (발행 순번, 거래 ID), 조건 지문 = (`since`, `until`, `limit`). 지금 `core/cursor.py` 를 그대로 쓴다
  - 응답 이름 바꾸기, 차감 행 수
- **repository**
  - PostgreSQL: 기준선 이후 발행 목록 (`version`·`published_at`·`publish_id`) — 작다
  - ClickHouse:
    ```sql
    WHERE publish_id IN {ids:Array(UUID)} AND (indexOf(ids, publish_id), trade_id) > ({r},{k})
    ORDER BY indexOf(ids, publish_id), trade_id LIMIT {n}
    ```
    `api_reader` 는 이미 `aptlake.*` SELECT 권한이 있다.
- **router:** `require_scope("bulk")` + 플랜 `allow_bulk` 확인, `limit ≤ max_page_size`, `respond()` 로 캐시·헤더·행 차감.
- **성능:** 피드 페이지 질의 p95 를 QA 스택 대형 데이터로 잰다. 기본 키 앞부분이 `publish_id` 라 범위가 좁다. 목표값은 측정 뒤 정한다.

## 4. F4 원천 반영 지연

### 4.1 L1·L2 — dbt 모델 `gold.source_latency` (매일 `dims_and_index` 에서, 전체 다시 계산)
```
part_first   = bronze.rtms_raw 에서 (sgg_cd, deal_ym) 별 min(fetched_at)          -- 파티션 첫 수집
versions     = silver.apt_trade + lag(valid_from) over (trade_id 별 version 순)
L1 사건      = version = 1 인 행: 관측일 = valid_from(KST 날짜), 기준 = deal_date
L2 사건      = trade_id 별 is_cancelled 가 처음 참인 버전: 관측일 = valid_from, 기준 = cancel_date
제외 사유    = initial_load (valid_from = part_first) | low_resolution (관측 때 계약월 나이 ≥ 3개월)
               | time_inverted (valid_from < 이전 버전 valid_from, D1) | no_cancel_date (L2)
출력 입도    = (metric, observed_date, sido_cd, reason, lag_days) → n
```
- ClickHouse `source_latency` 로 기존 `_exchange` 방식(소형 표 통째 교체)으로 발행한다. 입도가 일 단위 구간이라 행 수가 작다.
- 분위수는 API service 가 구간 분포에서 **정확히**(일 해상도) 계산한다. Trino `approx_percentile` 은 근사라 쓰지 않는다.
- 무거운 질의라 처음 실행 때 Trino 메모리를 `docker stats` 로 지켜본다(ADR-034).
- 매일 일정 `daily_dims_and_index` 가 켜져 있어야 갱신된다(`make backfill-start` 가 켠다).

### 4.2 L3 — 발행 때 기록 (기능 시작 후부터)
- `ops.publish_lag(publish_id, sgg_cd, deal_ym, fetched_at, published_at)` 를 1장 6단계 트랜잭션에서 넣는다.
- 대상: 이번 발행에 처음 반영되는 수집분, 즉 직전 기록 발행 이후 `last_changed_at` 이 바뀐 파티션.
- 분위수는 PostgreSQL `percentile_disc` 로 정확히 계산한다(분 단위).

### 4.3 API·화면
- `GET /v1/quality/latency` — `features/quality` 에 더한다.
  - repository: ClickHouse 구간 합계, PostgreSQL L3
  - service: 분위수·측정 시작일·`n` 이 0일 때 처리. 순수 함수라 시험한다.
- 웹 `QualityPage` 에 구역 하나를 더한다.
  - 지표 카드 3개 (p50·p90·`n`), 지표별 분포 막대, 표 보기, 해상도·제외 규칙 설명
  - 계산은 `web/src/domain/quality.ts` 에 두고 단위 시험한다
  - 색·막대 규격은 기존 차트 규칙과 대비 검사를 따른다(ADR-036)

## 5. F5 플랜·사용량·청구 단위

| 부분 | 변경 |
|---|---|
| `api.plan.public` (신규 열) | anonymous·free·pro = true, web·loadtest = false |
| `GET /v1/plans` | 기동 때 읽은 플랜 표 중 공개 플랜. 응답은 고정이라 캐시 가능 |
| `GET /v1/me/usage` | `limits` 에 한도 전부, `today` 에 이 키의 오늘 사용·남은 행. Redis `al:rows:{subject}:{KST 날짜}` 를 읽는다. 기존 `remaining_rows` 를 재사용한다 |
| `GET /v1/me/usage/monthly` | `usage_event` 를 고객·KST 월로 묶는다(요청·오류·차감 행·429 수) + `api.export_job` 의 달별 완료 건수·행 수. 최대 6개월(보관 180일) |
| 웹 `DeveloperPage` | 고정 표 삭제 → `/v1/plans`. 키 입력 뒤 오늘 행 사용률(키 기준), 30일 일별 요청·행(고객 기준), 달별 청구 단위 표. 이름표로 기준을 나눈다 |
| 가격 | 표시하지 않는다 (결정 4). 화면 문구: "공개 단가가 없는 학습용 서비스라 금액 대신 과금 대상 단위를 보입니다" |

## 6. 바뀌는 스키마·권한 목록

| 위치 | 변경 |
|---|---|
| PostgreSQL `V010__data_product.sql` | `ops.dataset_version.publish_id uuid UNIQUE`(기존 행 NULL) · `ops.dataset_month_file(publish_id, deal_ym, object_key, rows, bytes, sha256, created_at, deleted_at)` · `ops.publish_lag(…)` · `api.plan.public` · `api.export_job.dataset_version, sha256` |
| PostgreSQL 권한 | pipeline: `dataset_month_file` SELECT·INSERT·UPDATE(`deleted_at` 열만), `publish_lag` INSERT · api_app: 두 표 SELECT. 마이그레이션 권한 시험(`test_migrations.py`)에 넣는다 |
| ClickHouse `01-schema.sql` | `trade_change`, `source_latency` (`CREATE … IF NOT EXISTS`, ch-migrate 가 멱등 적용) |
| ClickHouse 사용자 | 변경 없음(publisher 가 쓰고 api_reader 가 읽음). 결정 5 검증 뒤 `exporter` 권한 축소 |
| MinIO `init.sh` | `snapshots` 버킷, `snapshot` 계정, `export` 계정에 `snapshots/*` Get |
| `.env.example` | `MINIO_SNAPSHOT_SECRET=` (이름만, 값은 `make init` 이 생성) |
| compose | dagster 에 snapshot 계정 환경변수, minio-init 에 비밀 전달 |

## 7. 시험 계획

| 층 | 내용 |
|---|---|
| 결함 먼저 | D1·D2·D3 각각 **실패 시험 커밋 → 수정 커밋**. D1 은 A→B→A 원본 순서를 넣은 단위 시험(+ QA 레이크 통합)으로 재현한다. 재현되지 않으면 미확인으로 남기고 F4 의 `time_inverted` 제외만 둔다 |
| 파이프라인 단위 | `diff_month` 분류(종류 5개, 복합 변경, 기준 없음) · 버전→파일 목록 규칙 · 보관 정리 대상 · 지연 사건 분류(제외 사유 4개) |
| dbt | `source_latency` 의 `not_null`·`accepted_values`(reason)·`lag_days ≥ 0` 검사 |
| 레이크 통합 | **QA 레이크에서만**(운영 레이크 아님): 발행 → 파일·변경·기록이 한 묶음으로 남는지, 6단계 실패 주입 뒤 다음 발행이 변경을 이어 받는지 |
| API (Testcontainers) | 피드: 404·410·403·빈 결과·커서 변조·상한 버전 고정·행 차감 / 내보내기: 버전 지정·만료·410 / 지연: 분위수 / 플랜·사용량: 키·고객 기준 분리, 304 미차감. 골든 응답 추가 |
| 수용 시험 | 명세 2.7-2(스냅숏 + 피드 적용 = 최신 서빙)를 Testcontainers 로 끝에서 끝까지 |
| 웹 | 화면 스냅숏 갱신, `domain` 단위 시험(분위수·단위 표기), 고정 플랜 숫자가 없는지 확인, 대비 검사 |
| 성능 | QA 스택(대형 데이터) — 피드 p95, 발행 시간 증가분(3·4단계), 기준선 작업 시간·파일 용량, 내보내기 메모리 최대값 → `docs/performance.md` |
| 보안 | 계정 최소 권한(snapshot 계정으로 raw·lake 접근 거부, export 계정으로 snapshots 쓰기 거부), 다른 고객의 내보내기·피드 접근 불가 |

## 8. 작업 순서 (단계마다 PR 하나, CI 통과 후 병합)

| 단계 | 내용 | 대략 크기 | 선행 |
|---|---|---|---|
| PR-1 | D1·D2·D3 (실패 시험 → 수정) | 작음 | — |
| PR-2 | 발행 ID·달 파일·기준선 작업·V010 일부·MinIO 계정 + F3 내보내기 | 큼 | PR-1 |
| PR-3 | F2 `trade_change`·비교·피드 API | 중간 | PR-2 |
| PR-4 | F4 dbt 모델·L3 기록·지연 API·품질 화면 | 중간 | PR-1(D1) |
| PR-5 | F5 플랜·사용량 API·개발자 화면 | 작음 | — |
| 마무리 | ADR·README·runbook·성능 문서·포트폴리오(새 판) | — | 전부 |

- 크기는 상대 비교다. 시간 추정은 하지 않는다.
- PR-5 는 앞 단계와 무관하다. 앞 단계가 막히면 먼저 할 수 있다.

## 9. 배포 순서와 사용자 작업

- 지금 운영 스택은 내려가 있다. 배포는 운영 스택을 다시 올린 뒤 아래 순서로 한다.
- 무거운 단계 전에는 `docker stats` 로 VM 여유를 확인한다. 다른 프로젝트(buildrisk-radar)가 함께 쓰고 있다.

1. `make init` — 새 내부 비밀(`MINIO_SNAPSHOT_SECRET`)을 자동 생성. 외부 키 등록은 필요 없다.
2. `docker compose up -d minio-init` — 버킷·계정 추가 (멱등)
3. `docker compose up -d --build api api-internal web` — db-migrate(V010)·ch-migrate 먼저 적용
4. `make pipeline-redeploy`
5. 기준선 작업 1회 (큐 경유, 새 `make` 대상): 서빙 70개월 → 달 파일 + 기준선 버전
6. 확인: 기준선 버전으로 내보내기 1건, 다음 발행 뒤 피드 1회, 다음 날 05:30 뒤 지연 화면

## 10. 위험과 대응

| 위험 | 대응 |
|---|---|
| 발행 시간이 늘어 수집 주기를 밀어냄 | 3·4단계 시간을 QA 에서 재고, 한 달 발행 시간 증가분을 성능 문서에 남긴다. 크면 비교를 ClickHouse SQL 로 옮기는 안을 다시 검토한다 |
| 스냅숏 용량 증가 | 기준선 뒤 실측 → 보관 정책 N 결정 → 주간 정리. `snapshots` 버킷 용량 확인 절차를 runbook 에 넣는다 |
| 피드 보존 TTL 과 API 판정 불일치 | 스키마·설정 값 일치 시험 |
| L1·L2 표본이 당분간 작음 (백필 이후 관측분만) | `n`·측정 시작일을 늘 함께 보이고, 0이면 숫자를 숨긴다 |
| D1 이 실제로 재현됨 | PR-1 에서 수정 방향을 ADR 로 정한다. 후보: silver 의 `ingested_at` 을 bronze 수집분 시각 대신 이번 적재의 수집 시각(`mark_loaded` 의 `fetched_at`)으로 |
| 운영 레이크 오염 | 통합 시험은 QA 레이크에서만. 운영에는 배포만 한다 |

## 11. 같이 고칠 문서
- ADR-043(발행 기록과 달별 스냅숏 파일 — F2·F3 의 공통 기반, 기각한 대안), ADR-044(변경분 피드), ADR-045(원천 반영 지연 정의·제외 규칙), ADR-046(플랜 공개·가격 비표시). D1 이 재현되면 그 결정도 ADR 로 남긴다.
- README: API 목록, 기능, 테스트 수, 측정값 · runbook: 기준선 작업, 피드·스냅숏 보관, 정리 작업 · 성능 문서: 7장 측정값.
- 포트폴리오: 기능이 끝난 뒤 새 판으로 "포지셔닝·비즈니스 모델" 쪽과 함께 반영한다 (기존 파일은 덮어쓰지 않음).
