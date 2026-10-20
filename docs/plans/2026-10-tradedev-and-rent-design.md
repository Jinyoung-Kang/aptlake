# 설계 계획서 — 매매 원천 상세판(TradeDev) 전환 · 아파트 전월세 실거래 추가

상태: **0단계 완료(2026-10-20) · 범위 (c) 로 구현 중** — 결과와 바뀐 결정은 [§6](#6-0단계-결과와-진행-결정-2026-10-20) · 작성 기준 커밋 `e0411ac` · 짝 문서: [decisions.md](../decisions.md) ADR-018 · 042 · 043 · 044 · 045 · 049 · 050

2026-10 에 새로 발급받은 공공데이터포털 API 가운데 이 프로젝트에 닿는 것은 둘이다.
- **(A) 국토교통부 아파트 매매 실거래가 상세 자료** `RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev` — 지금 쓰는 기본판의 20개 항목에 **법정동 코드 조각(`umdCd` · `landCd` · `bonbun` · `bubun`) · 도로명 주소 항목(`roadNm` · `roadNmSggCd` · `roadNmCd` · `roadNmSeq` · `roadNmbCd` · `roadNmBonbun` · `roadNmBubun`) · 단지 일련번호 `aptSeq`**(예 `11110-34`)가 더 온다.
- **(B) 국토교통부 아파트 전월세 실거래가 자료** `RTMSDataSvcAptRent/getRTMSDataSvcAptRent` — 같은 `LAWD_CD × DEAL_YMD` 요청, 항목은 `sggCd umdNm aptNm jibun excluUseAr dealYear/Month/Day deposit(만원 문자열) monthlyRent floor buildYear contractTerm contractType useRRRight preDeposit preMonthlyRent roadnm… aptSeq`(활용가이드 p.134).

솔직한 평가: **(A) 단독은 효과 대비 위험이 크다.** 얻는 것은 단지 식별 보강(`aptSeq`)과 도로명뿐인데, 지문 키 · 스키마 검사 · 내보내기 계약이 모두 걸린다.
**(B) 는 제품 범위를 넓히는 큰 작업**이고, 전세가율을 단지 단위로 제대로 내려면 (A) 의 `aptSeq` 가 있는 편이 낫다. 그래서 순서는 **0단계 탐침 → (B) 를 하기로 했을 때만 (A) 를 선행** 으로 둔다. (B) 를 하지 않기로 하면 (A) 도 하지 않는다.

## 0. 설계를 묶는 지금 구조의 사실 (코드로 확인)

| 사실 | 근거 | 설계에 주는 영향 |
|---|---|---|
| 원천 주소가 코드에 고정돼 있다(`rtms/client.py:28`, `tools/probe_rtms.py:11`). 설정 변수 없음 | `RtmsClient.URL` | 엔드포인트를 주입 가능하게 바꾸고 전월세용 두 번째 클라이언트 인스턴스를 만든다 |
| bronze 검사 `bronze_schema_matches_v1` 은 `SOURCE_FIELDS_V1` 밖의 키를 `unknown_fields` 로 보고 **격리**한다. 자산 검사 `_verify_loaded_bronze` 는 `schema_ver == 1` 을 요구 | `ingest.validate`, `definitions.py` | 상세판 응답은 그대로 받으면 전 파티션이 격리된다 → 버전별 필드 집합(`fields_for(schema_ver)`)으로 |
| 거래 지문 `trade_key` 는 8개 필드(sgg · umd · jibun · aptNm · 면적 · 거래일 · 금액 · 층)로, 바뀌면 공개 거래 ID 가 바뀌고 변경 피드가 넘친다 | `rtms/fingerprint.py`, ADR-042 | 새 항목은 지문에도 `MUTABLE_FIELDS` 에도 넣지 않는다(넣으면 270만 건이 모두 새 버전). 상세판이 8개 필드를 **바이트 단위로 같게** 주는지 먼저 확인 |
| MERGE 의 "속성 같음" 분기는 `last_seen_*` 만 갱신한다 | `silver.py` | 새 열을 기존 현재 행에 채우려면 그 분기에 `coalesce` 보강을 넣어야 한다 |
| 응답 해시가 바뀌면 파티션이 다시 LOADED → 병합된다. `next_due` 는 최근 3개월 매일, 12개월 안 30일, 그 뒤 90일 | `ops_db.next_due` | 전환 뒤 최대 약 90일 동안 v1 · v2 가 섞인다. 강제 재수집은 17,664회 = 백필 한도로 2.5~4.5일 |
| Iceberg 스키마 변경 장치가 없다(`lake.py DDL` 은 CREATE IF NOT EXISTS 만, dbt 는 `on_schema_change` 없음 = ignore). ADR-043 D2 는 "70개월 재발행" 때문에 열 추가를 피했다 | `lake.py`, `dbt_project.yml` | stage · silver 는 `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 를 `ensure_tables()` 에 멱등으로 추가. gold 는 열을 안 바꾸거나 full-refresh 를 계획 |
| `trade_current` 열 집합은 스냅숏 Parquet 스키마(`snapshots.SCHEMA`) · API `EXPORT_SCHEMA` · 변경 피드 `changes.FIELDS` 와 묶여 있다 | ADR-044 · 045, `test_services.py:461` | 서빙 열을 바꾸면 기준선 재설정이 필요 → 1차는 **silver 까지만** 보강 |
| 운영 상태에 **원천 차원이 없다**: `ops.ingest_partition PK (sgg_cd, deal_ym)`, `ops.month_state(deal_ym)`, 예산 `budget:rtms:{day}` · `ops.api_budget(source='rtms')`, 품질 · 운영 조회가 `source='rtms'` 와 `s3://raw/rtms/` 를 고정 | `ops_db.py`, `quality/repository.py`, `ops/repository.py` | 전월세는 상태 · 예산 · 원본 경로에 `source` 차원을 더하는 V013 마이그레이션이 먼저다 |
| 발행은 네 표 고정(`region_month · rollup_month · trade_current · trade_version`), `_partition/_part_col` 이 표 이름으로 분기, 버전은 발행마다 올려야 한다(ADR-034) | `publish.py` | 발행을 "원천별 표 목록" 으로 일반화하고, 전월세 발행도 버전을 올리되 매매 달 발행처럼 보이지 않게 `dataset_version` 에 `source` 를 둔다 |
| 일일 한도는 활용신청(API)별로 따로 센다(공공데이터포털 규칙) — 전월세는 별도 예산 | README §8 의 확인 방식 | `Budget(..., "rent")` 를 따로 두고 센서 `run_calls` 가 두 자산의 설정을 모두 읽게 한다 |
| 단지 차원 `silver.apt_complex` 는 `stage.apt_trade` 에서만 만들어진다. `complex_key` 는 이름 · 지번 해시 | `silver.COMPLEX_MERGE`, `fingerprint.complex_key` | 전월세만 있는 단지는 차원에 없다. 전세가율의 조인 키는 `complex_key`(지금) 또는 `aptSeq`((A) 뒤) |
| 추측 값 금지(ADR-018): 코드 의미 · 분류는 원천 · 공식 표로 확인한 것만 | `CLAUDE.md:24` | `contractType` · `useRRRight` 값의 뜻, 빈 값 형식, `numOfRows=9999` 지원은 **실응답으로 확인하기 전에는 쓰지 않는다** |
| 다음 번호: ADR-**049**, PostgreSQL 마이그레이션 **V013**. 골든 38건(README 상단 표는 36 으로 남아 있음) | `docs/decisions.md`, `api/migrations/`, `api/tests/golden/` | |

## 1. 0단계 — 탐침 (코드 변경 없음, 결과로 범위를 정한다)

1. **활용신청 확인**: 두 엔드포인트 모두 포털에서 승인됐는지. 승인 안 된 API 를 부르면 코드 20 → `KeyRejected` → **그날 매매 수집까지 멈춘다**(`budget.mark_exhausted`). 탐침은 파이프라인이 아니라 `tools/` 스크립트로만, 키는 `.env` 에서 읽고 출력하지 않는다.
2. **`tools/probe_tradedev.py`(신규, `probe_rtms.py` 복제)**: 같은 `(sggCd, ym)` 3개(서울 자치구 · 광역시 구 · 군 지역)를 기본판과 상세판에서 받아
   - 공통 20개 항목이 바이트 단위로 같은지(빈 값 공백 한 칸 · `YY.MM.DD` · 쉼표 금액 포함),
   - `(trade_key, dup_seq, attr_hash)` 집합이 같은지(지문 모듈을 그대로 적용),
   - 새 항목의 채움률(`aptSeq` 가 비는 행 비율, 도로명 없는 행), `aptSeq` ↔ `complex_key` 가 1:1 인지(한 `complex_key` 에 여러 `aptSeq`, 그 반대 모두),
   - `numOfRows=9999` · `totalCount` 동작이 같은지
   를 표로 남긴다(`docs/plans/` 또는 README §8 '확인한 원천 사실' 에).
3. **`tools/probe_rent.py`(신규)**: 전월세 3개 파티션을 받아 항목 집합 · 빈 값 형식 · `deposit`/`monthlyRent` 문자열 형식 · `contractType` · `useRRRight` · `contractTerm` 의 **실제 값 목록** · 월세 0 인 행 비율 · 해제 필드 유무 · `aptSeq` 유무 · 한 달 전국 건수(호출 · 행 수 추정)를 확인한다.
4. **게이트**: ① 상세판이 8개 지문 필드를 같게 주지 않으면 (A) 중단(공개 ID 가 바뀌는 전환은 하지 않는다). ② 전월세 항목 뜻이 원천 문서로 확정되지 않는 것(예 `contractType` 값)은 화면에 내지 않고 원문만 보관한다.

## 2. (A) 상세판 전환 — 설계

**원칙**: 공개 거래 ID · 변경 피드 · 내보내기 스키마는 바꾸지 않는다. 새 항목은 **"보강 전용 열"**(지문 · 속성 해시에 안 들어가고, 있으면 채우고 없으면 둔다)로만 다룬다.

| 영역 | 변경 |
|---|---|
| `rtms/client.py` | `RtmsClient(url=...)`. 매매 상세판 URL 은 설정 `RTMS_TRADE_URL`(기본 상세판), 이전 기본판은 롤백용 값으로 README 에 |
| `rtms/parse.py` | `SOURCE_FIELDS_V2 = V1 + 12개`, `SCHEMA_VERSION = 2`, `fields_for(schema_ver)`. `REQUIRED_FIELDS` 는 그대로(새 항목은 모두 선택). `TypedTrade` 에 `apt_seq · umd_cd · land_cd · bonbun · bubun · road_nm · road_nm_cd · road_nm_bonbun · road_nm_bubun`(나머지 도로명 코드 조각은 `row_json` 에만) |
| `ingest.validate` · `_verify_loaded_bronze` · `test_parse.test_real_page_parses_all_items` | 버전별 필드 집합. bronze 표는 `row_json` + `schema_ver` 라 **표 변경 없음** |
| `rtms/fingerprint.py` | **변경 없음**(지문 · `MUTABLE_FIELDS` 그대로). hypothesis 순서 무관 테스트 유지 |
| `lake.py ensure_tables()` | `ALTER TABLE lake.stage.apt_trade / lake.silver.apt_trade / lake.silver.apt_complex ADD COLUMN IF NOT EXISTS …`(멱등), `STAGE_SCHEMA` · `DATA_COLS` · `_trino_types()` 에 새 타입 |
| `silver.merge_sql` | "속성 같음" 분기에 `apt_seq = coalesce(s.apt_seq, c.apt_seq)` 식 보강(새 열 전부). `COMPLEX_MERGE` 는 `apt_complex.apt_seq` 를 같은 방식으로 채우고, 한 `complex_key` 에 서로 다른 `apt_seq` 가 오면 채우지 않고 경고 검사 `complex_aptseq_conflicts`(WARN) 에 센다 |
| gold · ClickHouse · API · 웹 | **1차(A1) 변경 없음**. 2차(A2, 별도 ADR): `trade_serving` · `complex_summary` 에 `aptSeq` · 도로명 노출, `trade_current(+_staging)` ALTER, `snapshots.SCHEMA` · `EXPORT_SCHEMA` · `changes.FIELDS` 버전 올림과 **기준선 재설정**(ADR-044 `snapshot_baseline` 다시) |
| 롤아웃 | 배포 뒤 `UPDATE ops.ingest_partition SET next_due_at = now()`(백필 우선순위, 한도 안에서 센서가 계획) → 2.5~4.5일에 수렴. 그동안 v1 · v2 혼재는 품질 화면의 `schema_ver` 분포로 보여 준다(품질 집계 쿼리에 `schema_ver` 그룹 추가) |
| 테스트 | 상세판 실응답 fixture `fixtures/rtms_dev_11110_202407.xml` · 파서 v2 · validate 버전별 · **동일 파티션 v1→v2 재수집 통합 테스트(QA 레이크)**: 새 버전 0건 · 보강 열 채워짐 · 변경 피드 0건 |
| 문서 | ADR-049(원천 전환 · 보강 전용 열 원칙 · 공개 ID 불변) · README §8 확인한 원천 사실 · §10 키 표 · `CLAUDE.md` 원천 목록 · runbook 첫 기동(활용신청 2건) |

## 3. (B) 전월세 실거래 — 설계

### 3-1. 운영 상태에 원천 차원 (V013, 먼저)

- `ops.ingest_partition`: `source varchar(12) NOT NULL DEFAULT 'rtms'` 를 PK 앞에(`(source, sgg_cd, deal_ym)`), 기존 행 백필. `ops.month_state(source, deal_ym)`. `ops.api_budget(source)` 는 이미 열이 있음(`'rent'` 값 추가). `ops.dataset_version` · `ops.dataset_month_file` · `ops.publish_lag` 에 `source`(기본 `'trade'`). `ops.dq_result.partition` 은 `rent:{sgg}/{ym}`(17자 ≤ 20). `last_ingest_id varchar(40)` 는 `ingest_id` 접두어 없이 두고 source 열로 구분.
- 바뀌는 코드: `ops_db.*`(모든 함수에 `source` 인자, 기본 `'rtms'`), 센서 SQL, `scheduling.run_calls`(두 자산 설정을 모두 읽음), 품질 · 운영 · 관리자 재시도 API(`source` 쿼리 인자, 기본 `rtms`), 백업 검증, `RawArchive` 접두어 `rtms_rent/`.

### 3-2. 파이프라인

| 구성 | 내용 |
|---|---|
| 파싱 `rtms/rent_parse.py` | `RENT_FIELDS_V1`(탐침으로 확정) · `TypedRent`(deposit · monthly_rent 는 만원 정수 → 원 단위 변환은 gold 에서, `contract_term` 원문, `contract_type` · `use_rr_right` 원문 보존 + 확인된 값만 코드화, `pre_deposit` · `pre_monthly_rent`, `apt_seq`) · `REQUIRED_RENT_FIELDS = sggCd umdNm aptNm dealYear dealMonth dealDay deposit` |
| 지문 `rtms/rent_fingerprint.py` | `rent_key = sha256(sgg · key_umd · norm_jibun · norm_text(aptNm) · 면적 · 계약일 · 보증금 · 월세 · 층)` + `dup_seq`; `complex_key` 는 매매와 **같은 함수**(같은 `umd_aliases`); 가변 필드 = 갱신 구분 · 갱신요구권 · 종전 보증금/월세 · 계약기간 · 건축년도(해제 필드는 탐침 결과에 따라). 전월세는 해제 신고가 없을 수 있어 `missing_since` 의 뜻을 ADR 에 따로 적는다 |
| 수집 `ingest_rent.py` | `ingest.py` 의 `_fetch_all` · `validate` · bronze 적재를 **원천 설정 객체**(URL · 필드 집합 · bronze 표 · raw 접두어 · 예산 이름)로 매개변수화해 공유. 예산 `Budget(..., "rent", RENT_DAILY_LIMIT × RENT_BUDGET_PCT)`, 풀 `rtms`(TPS 공유), `PROVISIONAL_DAYS` 는 전월세용 값을 따로(탐침 뒤 결정) |
| 레이크 표 | `bronze.rtms_rent_raw`(매매와 같은 스키마) · `stage.apt_rent` · `silver.apt_rent`(SCD2, 같은 MERGE 생성기 — `merge_sql(spec)` 로 표 · 열 · 키를 받게 리팩터링) · `maintenance.TABLES` 에 추가 |
| 자산 | `bronze/rtms_rent_raw` · `silver/apt_rent`(검사: 중복 현재 키 0 · stage 대조 · 결측 관측 · 지문 중복 그룹) · `gold_rent_monthly`(dbt) · `serving/clickhouse_rent_month`; Job `rent_month_pipeline`, 센서 `due_rent_partitions_sensor`(같은 `plan_runs`, 예산 이름만 다름). `max_concurrent_runs: 1` 이라 매매 뒤에 줄을 선다 — 하루 안에 두 원천이 다 돌도록 센서 간격 · 상한을 재는 것이 운영 과제 |
| dbt gold | `rent_serving`(현재 계약, `deposit_won` · `monthly_rent_won` · `is_jeonse = monthly_rent = 0`(탐침으로 확인된 정의만) · `deposit_ppm2`) · `region_rent_month`(sgg×월: 계약 · 전세 · 월세 · 갱신 · 갱신요구권 사용 건수, 전세 보증금 ㎡당 p25/중앙/p75, 월세 중앙, `low_sample` < 5) · `region_rent_rollup_month` · `complex_rent_summary`(단지 × 최근 12개월) · **`region_jeonse_ratio_month`** = 같은 sgg · 같은 달 창(최근 3개월)의 전세 보증금 ㎡당 중앙값 ÷ 매매 ㎡당 중앙값, 두 쪽 표본 ≥ 5 일 때만(`region_month.priced` 재사용) · **`complex_jeonse_ratio`** = `complex_key`(또는 `apt_seq`) × 전용면적 구간(±3㎡ 같은 면적형) 기준, 최근 12개월, 표본 ≥ 3 — **구간 폭 · 창 · 최소 표본은 ADR-050 으로 정하고 화면에 그대로 표시**. 테스트는 매매와 같은 generic(`unique_combination` · 항등식 · 분위수 순서) |
| ClickHouse | `rent_current` · `region_rent_month`(연 파티션, ADR-037) · `rent_rollup_month` · `jeonse_ratio_month` · `complex_rent`(+ `_staging`), `publish_month(source)` 로 표 목록 일반화, 버전은 공통 `gold@…` 를 올리되 `dataset_version.source='rent'` |

### 3-3. API · 웹

- `api/features/rent/`(router · service · repository — 아키텍처 테스트가 요구), `require_scope("read")`, `respond()` 경유, 집계는 행 0 차감:
  `GET /v1/regions/{sggCd}/rent/months` · `GET /v1/regions/{sggCd}/rent/distribution` · `GET /v1/rents`(필터 매매 `/v1/trades` 와 동일 + `kind=jeonse|wolse`) · `GET /v1/rents/{rentId}/history` · `GET /v1/regions/{sggCd}/jeonse-ratio` · `GET /v1/complexes/{complexKey}` 응답에 `rent` 요약 블록 추가. `bulk`: `/v1/rents/changes` 는 2차. 면책 문구는 `DISCLAIMER` 그대로 + 전세가율에 "자체 산출 · 표본 조건" 문장. 골든 케이스 추가(`UPDATE_GOLDEN=1`).
- 웹: `RegionPage` 탭 `"rent"`(전월세 추이 · 전세가율 · 갱신 비율), 시장 개요 지도 `domain/market.ts METRICS` 에 `jeonseRatio`, 단지 화면에 전월세 섹션, 거래 화면 토글(매매/전월세) 또는 별도 메뉴 `rents`(`App.tsx` · `TITLES` · `Shell NAV`), `api/endpoints.ts` · `types.ts` · `domain/rent.ts` + 테스트, `test/app.tsx route()` 매핑 · 스냅숏 `PAGES`.
- 품질 화면: 원천 선택(매매/전월세) · 전월세 원천 반영 지연은 2차.

### 3-4. 비용 · 운영

- 백필 2021-01~현재: 69개월 × 256 = 17,664회(전월세 예산, 매매와 별도) → 한도 8,000/일 기준 2.5~3일. 전월세 건수는 매매의 1.5~3배로 알려져 있으나 **탐침으로 측정** — ClickHouse 행 수 · 디스크 · 발행 시간(`docs/performance.md` 형식으로 재측정).
- 시작 시점은 결정 사항(§4). 2023-01 부터면 호출 1/3.

### 3-5. 테스트 · 문서

- 파이프라인: 전월세 파서 fixture · 지문 hypothesis · ingest 공유 코드 회귀(매매 fixture 그대로 통과) · `test_ops_db_pg`(source 차원) · `test_publish_ch`(새 표 · 일반화된 발행) · QA 레이크 통합(`99999/209901` 전월세 판).
- API 골든 · 아키텍처 테스트 · 마이그레이션 권한 테스트(V013). 웹 스냅숏.
- 문서: ADR-049(A) · ADR-050(전월세 모델 · 전세가율 정의 · 운영 상태 source 차원) · README 상단 표(데이터 · 테스트 수 · ADR 수, 골든 36→38 정정) · §2 mermaid · §4 품질 게이트 · §7 메뉴 · §8 원천 사실 · §10 키 표 · §11 테스트 · §12 한계 · runbook · `CLAUDE.md`.

## 4. 승인이 필요한 결정 (권장안)

| # | 결정 | 선택지 | 권장 |
|---|---|---|---|
| 1 | 범위 | (a) 하지 않음 (b) (A)만 (c) (A)→(B) | **(a) 또는 (c)**. (b) 는 위험 대비 효과가 작다. AptLake 를 '매매 레이크' 로 완결시키려면 (a), '주택 실거래 데이터 플랫폼' 으로 넓히려면 (c) |
| 2 | (A) 의 서빙 노출(A2) | 1차에 포함 / 별도 | **별도** — 기준선 재설정이 필요해 ADR 과 재발행 계획을 따로 |
| 3 | 전월세 수집 시작 | 2021-01(매매와 같음) / 2023-01 | 전세가율은 매매와 같은 창을 쓰므로 **2021-01**(호출 17,664회, 별도 예산) |
| 4 | 전세가율 정의 | 지역: 3개월 창 · 표본 5 / 단지: 면적 구간 · 12개월 · 표본 3 | 위 값으로 시작하고 ADR-050 에 근거 · 민감도 표를 남긴다 |
| 5 | `contractType` · `useRRRight` 의 코드화 | 원문만 보관 / 확인된 값만 코드 | **확인된 값만**(ADR-018). 포털 문서에 값 목록이 없으면 원문 그대로 노출 |

## 5. 위험

- (A) 전환 중 v1 · v2 혼재 기간에 `complex_aptseq_conflicts` 가 늘 수 있다(같은 단지의 표기 차이) — 채우지 않고 경고로만.
- (B) 는 운영 상태 · 예산 · 발행 · 품질 조회의 공통 코드를 넓게 건드린다. V013 과 공유 코드 리팩터링을 **매매 동작이 그대로인지 골든 · 통합 테스트로 먼저 고정**한 뒤 전월세를 얹는다.
- 전월세 신고는 2021-06 이후 의무화라 그 전 기간은 표본이 적다 — 전세가율의 `low_sample` 표시가 중요하다.
- 공개 거래 ID(`trade_id`) 와 단지 ID(`c_…`) 는 바꾸지 않는다. `aptSeq` 는 보조 식별자로만 쓴다.

## 6. 0단계 결과와 진행 결정 (2026-10-20)

탐침: `tools/probe_tradedev.py`(호출 6회) · `tools/probe_rent.py`(호출 9회). 두 API 모두 활용신청 승인 상태였다(코드 20 없음).

| 확인 | 결과 |
|---|---|
| 게이트 ① 상세판이 지문 8개 필드를 같게 주는가 | **통과** — 3개 파티션 공통 20개 항목 바이트 동일, `(trade_key, dup_seq, attr_hash)` 집합 동일 |
| `aptSeq` ↔ `complex_key` | 1:1 (21·133·20개). 전월세의 같은 `aptSeq` 와 단지 키도 129/129 같음 → **전세가율 조인은 단지 키로 충분** |
| 전월세 항목 | 25개, 해제 항목 없음, 도로명은 소문자 이름(`roadnm`…), 빈 값 공백 한 칸 |
| 전월세 값 목록 | `contractType` = `신규`·`갱신`·공백, `useRRRight` = `사용`·공백, `monthlyRent` 는 늘 숫자이고 `0` 이 52.6~60.5% |
| 전월세 규모 | 같은 파티션 매매의 2.0~2.7배(합 2.10배), 큰 파티션도 한 쪽(1,877건). 2021-01 은 `contractType` 97% 공백 |
| 전월세 동일 지문 그룹 | 행의 5~17% — 매매보다 흔하다(동명 항목이 없음) |

진행 결정 (§4 권장안 기준, 0단계 결과로 조정한 것은 굵게):
1. 범위 (c) — (A) 를 먼저, 그다음 (B).
2. (A) 의 서빙 노출(A2)은 별도. **전 파티션 강제 재수집도 하지 않는다** — 조인 키가 단지 키로 충분해 서두를 이유가 없고 재수집 주기(최대 90일) 안에 채워진다.
3. 전월세 수집은 2021-01 부터(`BACKFILL_FROM` 공유, 별도 예산).
4. 전세가율: 지역 3개월 창·양쪽 표본 5, 단지 같은 전용면적·12개월·표본 3. **gold 표가 아니라 서빙 시점에 두 발행 표(`trade_current`·`rent_current`)에서 정확 분위수로 계산한다** — 두 원천의 발행 시점이 달라 gold 에 두면 한쪽 발행 뒤 낡은 비율이 남는다(ADR-050).
5. `contractType`·`useRRRight` 는 원문 그대로 보관·노출. 값이 원천의 낱말(`신규`·`갱신`·`사용`)이라 그 원문으로 세고, 공백은 '미상'. 전세 = `monthlyRent = 0` 인 계약(정의로 밝혀 쓴다).
