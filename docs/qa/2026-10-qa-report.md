# AptLake 출시 기준 QA 보고서 (2026-10)

- 브랜치 `qa/2026-10-release` (main d70042c 에서 분기)
- 계획: [2026-10-qa-plan.md](2026-10-qa-plan.md)
- 증거: [evidence/](evidence/)

## 1. 요약
- **결함 16건**
  - 심각도별: 치명 0, 높음 1, 보통 10, 낮음 5
  - 처리: 14건은 실패 시험 → 근본 원인 수정 → 같은 시험 통과, 2건(QA-005·009)은 미수정 권고
    - 출시 뒤 QA-005 를 후속으로 고쳤다(13장). QA-009 는 일부 고쳤고 목표는 아직 미달이다(14장)
  - 보안 결함: 2건(QA-001·004) 수정, 1건(QA-005) 권고 → 후속 수정
  - 독립 검토가 부분·미수정으로 찾아낸 QA-013·014 는 다시 열어 고쳤다(12장)
  - 데이터 손실·오염: 재현된 것 없음
- **출시 판단**
  - 로컬 단일 노드 운영 기준(지금 배포 형태: 모든 포트 127.0.0.1): **출시 가능**. 이 브랜치를 배포한다는 조건이다.
  - 공개 인터넷 서비스 기준: **조건부**. 근거와 조건은 8장에 있다.
- **시험:** 기존 313개 → **348개**, 전부 통과 (후속 수정 뒤 354개 — 14장)
  - 결함 재현 시험 34개(API 23·웹 11)와 시험이 없던 경로 1개(파이프라인)를 더했다.
  - 재현 시험 가운데 2개(QA-012 둘째·QA-015 넷째)는 수정 전에도 통과하는 회귀 방지용이다.
  - 운영 레이크 통합 시험 2개는 QA 레이크에서 통과했다.

## 2. 검증 범위와 방법
| 영역 | 방법 | 대상·규모 |
|---|---|---|
| 환경 | 운영과 완전히 분리한 QA 스택(`qa/docker-compose.qa.yml` — 볼륨·네트워크·포트·이미지 태그 분리) + Testcontainers. **운영 스택에는 시험 요청을 보내지 않았다** | 생성 데이터: 시군구 256 × 69개월, 거래 2,705,106, 조작 문자열(XSS·SQL·템플릿·긴 이름·이모지·비밀값 모양 오류 메시지) |
| 기능 | 매개변수 퍼징(`qa/probes/fuzz_api.py`), 값 대조(`correctness.py`), 경계값 | 14경로 × 조작값 47종 = 1,598요청 (수정 전·후), 값 대조 970건(두 시드), 경계 26건 |
| 보안 | 권한 상승·IDOR·관리 경로 변형·인젝션·XSS·CSP·CORS·CSRF·한도 우회·비밀값·EOL·취약점 | BFF 위조 헤더, 다른 클라이언트의 내보내기·사용량, 관리 경로 변형 20종, XFF 위조, 동시 120건, 인증 실패 40회, gitleaks(커밋 101), 이미지 11종 Trivy |
| 신뢰성 | 부하 중 의존 구성요소 멈춤(`chaos.py`), 잠금, 재시작, 동시 요청, 백업→복원 | ClickHouse·Redis·PostgreSQL 15·40초 멈춤, PG 표 잠금, API·작업자 재시작, 같은 요청 50건, QA 스택 백업 + 새 장비 복원 훈련 |
| 성능 | k6(QA 네트워크 안), query_log 질의 비용, Lighthouse | 200+100·80+40 RPS 각 2회, 혼합 8경로 2회, 주요 5화면 × 모바일·데스크톱 |
| 화면·접근성 | 실제 Chrome(puppeteer·내장 브라우저): axe WCAG 2.1 AA, 콘솔·실패·중복 요청, 상호작용, 키보드, 폭 375·1280, 밝게·어둡게 | 10화면 × 2폭 × 2테마 = 40조합, 상호작용 18조합 448~450회 클릭, Tab 40회 × 9화면 |
| 회귀 | 기존 시험 전체 + 레이크 통합 시험(QA 레이크) | 아래 3장 |

외부 원천 API(국토부·행안부·R-ONE·V-World)는 부르지 않았다. 저장된 응답과 목으로만 검증했다.

## 3. 실행한 시험과 결과
| 묶음 | 전 | 후 | 결과 |
|---|---|---|---|
| 파이프라인 단위 | 75 | 76 (+ 발행 교체 도중 끊김 뒤 재발행 회복) | 통과 |
| 파이프라인 통합 (레이크) | 2 | 2 — **QA 레이크**에서 실행 | 통과 |
| API (단위 + Testcontainers 통합) | 141 | 164 (+ QA 재현 23) | 통과 |
| 웹 (Vitest, 스냅숏 11 포함) | 73 | 84 (+ QA 재현 11) | 통과 |
| dbt | 22 | 22 — `dbt parse` 만 (데이터 시험은 미실행, 7장) | parse 통과 |
| 정적 검사 | ruff·mypy·tsc·Biome·색 대비 150쌍 | 같음 | 통과 |
| 의존성 취약점 | pip-audit(파이프라인·API)·npm audit | 같음 | 0건 |
| QA 점검 스크립트 | — | 퍼징·값 대조·장애 주입·BFF 권한·복원 훈련·화면 40조합·Lighthouse 10조합 | 수정 후 모두 통과 (QA-005·009 제외) |

## 4. 결함 목록 (심각도순)
심각도 기준은 다음과 같다.
- 치명: 인증 우회·데이터 손실/오염
- 높음: 주요 기능 오작동·악용 가능한 보안 약점
- 보통: 경계·부분 오류, 우회 가능
- 낮음: 표시·사소함

환경은 별도 표시가 없으면 다음과 같다.
- QA 스택(macOS M1, Docker VM 7.75GB·CPU 4, ClickHouse 26.8, 생성 데이터)
- 또는 Testcontainers

| 번호 | 심각도 | 영역 | 제목 | 상태 |
|---|---|---|---|---|
| QA-001 | 높음 | 보안 | 공개 웹으로 누구나 오류 로그 비우기·되돌리기 (ops 권한 상승) | 수정 (사용자 결정) |
| QA-002 | 보통 | 기능·신뢰성 | 범위 밖 연도(0000·9999-12) → 500, 같은 연결의 다음 요청도 끊김 | 수정 |
| QA-005 | 보통 | 보안 | 이미지 안의 수정판 있는 HIGH·CRITICAL 취약점, CI 이미지 스캔 없음 | 후속 수정 (13장) |
| QA-006 | 보통 | 기능 | 전국 전체 기간 대량 내보내기가 메모리 상한에서 실패 | 수정 |
| QA-007 | 보통 | 신뢰성 | ClickHouse 무응답 시 시간 초과 없음 (기본 300초) | 수정 |
| QA-008 | 보통 | 신뢰성 | PostgreSQL 잠금을 무기한 기다림 | 수정 |
| QA-009 | 보통 | 성능 | 200+100 RPS 목표(NFR-04) 미달 | 후속 일부 수정, 목표 미달 (14장) |
| QA-010 | 보통 | 접근성 | 글 속 링크가 색으로만 구분 (WCAG 1.4.1) | 수정 |
| QA-011 | 보통 | 접근성 | 스크롤 영역에 키보드로 접근 불가 (WCAG 2.1.1) | 수정 |
| QA-013 | 보통 | UX·성능 | 데이터가 늦게 오며 화면이 크게 밀림 (CLS 0.21~0.83) | 수정 (검토 뒤 보완) |
| QA-015 | 보통 | 기능 | 지역 분석에서 긴 기간을 고르면 422 ('전체' 버튼 포함) | 수정 |
| QA-003 | 낮음 | 기능 | 숫자 패턴이 유니코드 숫자(전각·아랍-인도)를 받음 | 수정 |
| QA-004 | 낮음 | 보안 | /docs 가 버전 고정·SRI 없이 외부 스크립트를 실행 | 수정 |
| QA-012 | 낮음 | 성능 | 화면마다 시세 띠 요청이 두 번 | 수정 |
| QA-014 | 낮음 | 접근성 | 로고 링크·테마 버튼 이름이 보이는 글자를 담지 않음 (WCAG 2.5.3) | 수정 (검토 뒤 다시 열어 고침) |
| QA-016 | 낮음 | 접근성 | 팝업을 Esc 로 닫으면 초점이 사라짐 | 수정 |

### QA-001 (높음, 보안) 공개 웹으로 누구나 오류 로그 비우기·되돌리기
- **재현:** QA 스택에서 키 없이 다음을 보낸다.
  - `curl -X POST -H 'Sec-Fetch-Site: same-origin' http://127.0.0.1:3710/v1/ops/errors/clear`
  - `GET /v1/ops/errors` 도 같은 헤더로 보낸다.
- **기대:** ADR-031 대로 익명에게 ops 쓰기가 막혀야 한다.
- **실제:** 200 이다. 로그가 비워지고(`{"cleared":{"at":…}}`), DELETE 로 되돌릴 수 있고, 내부 오류 로그·연결 점검도 읽힌다.
  - 감사 기록은 actor `web:<IP HMAC>` 이고 client_ip 는 비어 있다.
  - 교차 출처(`Sec-Fetch-Site: cross-site`)는 403 이라 CSRF 는 막혀 있다.
- **원인:**
  - [provision.py:47](../../api/src/aptlake_api/provision.py) 웹 키 권한 `["read","ops"]`
  - [aptlake.conf.template:9](../../web/templates/aptlake.conf.template) 위조 가능한 헤더로 웹 키를 붙임
  - [ops/router.py:76·81](../../api/src/aptlake_api/features/ops/router.py) 보기·쓰기가 같은 권한
- **근거의 충돌:** 수정 전에는 헤더 위조 없이도 공개 웹 화면의 버튼만으로 비울 수 있었다. 이는 ADR-032 가 의도한 화면 동작이기도 해서, ADR-031(익명에게 ops 를 막는다)과 서로 맞지 않았다. 그래서 사용자에게 방향을 물었다.
- **결정·수정(사용자 선택):** 보기는 공개, 비우기·되돌리기는 운영자 키로 나눈다.
  - `ops_read` 신설(V008), 웹 키 = `read`·`ops_read`
  - 쓰기는 `ops` 만, 화면은 운영자 키를 입력받는다
- **증거:** [qa-s1-ops-via-bff.txt](evidence/qa-s1-ops-via-bff.txt), [qa-s1-ops-clear-via-bff.txt](evidence/qa-s1-ops-clear-via-bff.txt), [qa-001-bff-before.txt](evidence/qa-001-bff-before.txt) → [qa-001-bff-after.txt](evidence/qa-001-bff-after.txt), [qa-001-ui-after.txt](evidence/qa-001-ui-after.txt)
- **시험:**
  - `api/tests/test_qa_2026_10.py::test_qa_001_*`
  - `web/src/api/client.test.ts`(QA-001)
  - `qa/probes/security_bff.sh`, `qa/probes/ui_ops_clear.mjs`
  - 커밋: 3246adc → 1263bdc
  - API 실패 시험은 결함을 직접 재현하지 않는다(수정 전에는 `ops_read` 가 없어 키 발급부터 실패). 재현 근거는 `security_bff.sh` 의 수정 전·후 결과다.

### QA-006 (보통, 기능) 전국 전체 기간 대량 내보내기 실패
- **재현:** pro·bulk 키로 `POST /v1/exports {"from":"2021-01-01","to":"2026-09-30"}` 을 보낸다.
- **기대:** pro 플랜은 기간 제한이 없으므로 Parquet 이 만들어져야 한다.
- **실제:** 약 2초 만에 작업 `failed`, 오류는 `DatabaseError` 한 단어다.
  - ClickHouse 에는 `MEMORY_LIMIT_EXCEEDED`(384 MiB > 381 MiB, MergeSortingTransform)가 남는다.
  - 5년(읽은 행 235만)은 373·395 MB 로 상한(400,000,000 바이트) 직전이다. 거래가 매달 늘어 내보낼 수 있는 기간이 줄어든다.
  - 심각도는 보통이다. 기간이나 시군구로 나누면 우회할 수 있다(독립 검토 의견 반영, 처음에는 높음으로 적었다).
  - 운영 거래 표(269만 행)도 같은 규모다.
- **원인:**
  - [storage.py:75](../../api/src/aptlake_api/features/exports/storage.py) 전체 `ORDER BY`
  - [aptlake-users.xml:23](../../infra/clickhouse/users.d/aptlake-users.xml) exporter 프로필에 외부 정렬 설정이 없다.
  - 26.8 은 비율 설정(기본 0.5)이 있으면 바이트 값이 쓰이지 않는다.
- **수정:** `max_bytes_before_external_sort` 150MB + `max_bytes_ratio_before_external_sort` 0. 270만 행에서 최대 216MB·3.9초이고, API 경유로 255만 행 완료를 확인했다.
- **증거:** [qa-d4-export-failure-cause.txt](evidence/qa-d4-export-failure-cause.txt)
- **시험:** `api/tests/test_qa_export_memory.py` (운영과 같은 users.d, 300만 행). 커밋: 1c49342 → 6c9d874

### QA-002 (보통) 범위 밖 연도 → 500
- **재현:** 다음 요청을 보낸다.
  - `GET /v1/regions/41135/months?from=0000-01&to=2024-12`
  - 같은 형태로 `distribution?ym=9999-12`, `market/overview?ym=0000-01`, 품질 경로
- **기대:** 400 INVALID_PARAMETER.
- **실제:** 500 INTERNAL 이고 로그에 `ValueError: year 0 is out of range` 가 남는다. 같은 퍼징에서 연결 끊김(`Server disconnected`) 3건이 함께 났고, 순차로 다시 보내면 재현되지 않았으며 수정 후 0건이다. 500 뒤 keep-alive 연결이 닫혀서라고 보지만, 인과는 정황뿐이다.
- **원인:**
  - [params.py:9](../../api/src/aptlake_api/core/params.py) 연도 범위 없음
  - [values.py:12·17](../../api/src/aptlake_api/core/values.py) `date(0,…)`·10000년
- **수정:** `YM_Q` 연도를 1900~2099 로 제한하고, 관리 경로도 같은 패턴을 쓴다.
- **증거:** [qa-fuzz-api.jsonl](evidence/qa-fuzz-api.jsonl) (500 12건·끊김 3건) → [qa-fuzz-api-after-summary.txt](evidence/qa-fuzz-api-after-summary.txt) (5xx 0)
- **시험:** `test_qa_002_*` 10건. 커밋: fd9204b → 7a78b21

### QA-005 (보통, 보안) 이미지 안의 수정판 있는 HIGH·CRITICAL 취약점 — 후속 수정 (13장)
- **재현:** `trivy image --severity HIGH,CRITICAL --ignore-unfixed` 를 QA·운영 이미지에 돌린다.
- **실제:**
  - 자체 이미지(api·pipeline): CRITICAL 3·HIGH 6
    - Debian `libpcre2-8-0` deb13u2 → u3 수정판이 있다.
    - `psycopg-binary` 3.3.6(최신) 휠이 묶어 온 `libpcre2 10.32`(AlmaLinux 8)와 지원이 끝난 `libcrypto 1.1.1k` 가 있다.
  - 웹 이미지: HIGH 1
  - 서드파티 이미지: trino(netty CRITICAL 등 31), postgres(gosu Go stdlib CRITICAL 1·HIGH 21), silo(amqp091 CRITICAL 3), redis·grafana·prometheus·clickhouse(HIGH 1~5)
  - CI 는 소스 트리만 Trivy 로 검사하고 빌드한 이미지는 검사하지 않는다.
- **판단:** 네트워크로 닿는 경로는 확인하지 못했다. 예: pcre2 는 libselinux 용, gosu 는 기동 때만 쓴다. 그래도 수정판이 있어 보통으로 둔다.
- **권고:** 기반 이미지 다이제스트 갱신, CI 에 이미지 Trivy 스캔(예외 목록 포함), `psycopg[c]`(Debian libpq) 검토, 서드파티 태그 갱신.
- **증거:** [qa-s7-trivy-images.txt](evidence/qa-s7-trivy-images.txt), [qa-005-psycopg-bundled-libs.txt](evidence/qa-005-psycopg-bundled-libs.txt)
- **시험:** 실패 시험 대신 위 Trivy 명령(이미지 스캔이라 단위 시험으로 만들지 않음). 후속 수정에서 `tools/scan_images.sh` 와 CI 이미지 스캔으로 만들었다.

### QA-007 (보통, 신뢰성) ClickHouse 무응답 시 시간 초과 없음
- **재현:** 20 rps 부하 중 `docker pause aptlake-qa-clickhouse-1` 15초.
- **기대:** 기한 안에 503 으로 실패한다(서버 질의 상한 5초, Redis 는 2초에 503).
- **실제:** 모든 요청이 멈춘 시간만큼(최대 15.9초) 기다렸다가 200 이 났다. 클라이언트 기본값 300초까지 매달릴 수 있고, 웹은 30초 뒤 504 다.
- **원인:** [resources.py:43·52](../../api/src/aptlake_api/core/resources.py) `send_receive_timeout` 미지정
- **수정:** 연결 3초·응답 10초. 효과는 '긴 멈춤의 상한'이다.
  - 15초 멈춤: 최대 16.4초로 수정 전(15.9초)보다 줄지 않았다. 빈 연결을 기다리는 시간은 응답 상한에 들어가지 않기 때문이다.
  - 40초 멈춤: 가장 오래 걸린 요청이 16.5초였다. 수정 전에는 멈춘 시간 전부를 기다렸다(시험에서 25초 넘게 무응답).
  - '5초 근처에서 503'은 달성하지 못했다. 풀 대기까지 묶으려면 질의 단위 전체 시간 상한이 더 필요하다(개선 제안 A10).
- **증거:** [qa-r1-dependency-pause.txt](evidence/qa-r1-dependency-pause.txt) → [qa-r1-dependency-pause-after.txt](evidence/qa-r1-dependency-pause-after.txt), [qa-r1-clickhouse-pause40-after.txt](evidence/qa-r1-clickhouse-pause40-after.txt)
- **시험:** `test_qa_007_*` (시험 스택의 ClickHouse 컨테이너를 실제로 멈춤). 커밋: 73494a1 → abfaef9

### QA-008 (보통, 신뢰성) PostgreSQL 잠금을 무기한 기다림
- **재현:** `BEGIN; LOCK TABLE ops.log_view IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(20)` 동안 `GET /v1/ops/errors`.
- **기대:** 짧은 시간 안에 503.
- **실제:** 18.3초 뒤 200 이다. 연결 풀(10개)이 차면 다른 PG 경로도 멈출 수 있다(측정하지 않은 추정).
- **원인:** [resources.py:25](../../api/src/aptlake_api/core/resources.py) 질의·잠금 상한 없음
- **수정:** `statement_timeout` 5초·`lock_timeout` 2초. 시험에서 약 2초에 503 이다. 서버 프로세스 자체가 멈추면 서버가 상한을 집행하지 못한다(한계).
- **증거:** [qa-r1-pg-lock.txt](evidence/qa-r1-pg-lock.txt)
- **시험:** `test_qa_008_*`. 커밋: 73494a1 → 2ba6481

### QA-009 (보통, 성능) NFR-04 목표 미달 — 후속 일부 수정 (14장)
- **재현:** `qa/load/run_k6.sh t300 api.js RATE_MONTHS=200 RATE_TRADES=100`
- **기대:** months p95 < 80 ms, trades p95 < 150 ms.
- **실제:** 처리량 261.5·286.4 req/s, months p95 2,045·1,489 ms, trades p95 3,467·2,432 ms 다. 120 RPS 에서도 두 번 모두 미달이다(months 87~199 ms, trades 164~311 ms).
- **원인(측정):**
  - CPU 4개 VM 을 k6·API·ClickHouse 가 나눠 쓴다.
  - 질의당 고정 비용이 4~6 ms 다. region_month 의 인덱스 간격을 줄여 읽는 행을 91% 줄여도 CPU 는 그대로였다.
  - 같은 요청이 몰릴 때 합쳐 주지 않는다(개선 제안 A2).
- **증거:** [load/](evidence/load/), [qa-p-slow-queries.txt](evidence/qa-p-slow-queries.txt), [qa-p-region-month-granularity.txt](evidence/qa-p-region-month-granularity.txt)

### QA-010 (보통, 접근성) 글 속 링크가 색으로만 구분
- **재현:** `QA_TOOLS=… node qa/probes/ui_a11y.mjs --assert`
- **실제:** 40조합 모두 axe `link-in-text-block`(serious)에 걸렸다. 경로 표시줄·바닥글·문단 링크다.
- **원인:** [theme.css:110](../../web/src/theme.css) `a { text-decoration: none }`
- **수정:** 글 속 링크에 밑줄을 친다.
- **증거:** [qa-ui-a11y-summary.txt](evidence/qa-ui-a11y-summary.txt) → [qa-ui-assert-final.txt](evidence/qa-ui-assert-final.txt) (문제 0/40)
- **커밋:** b57be8f → e18f315 (+ 린트 후속 d1ec95f)

### QA-011 (보통, 접근성) 스크롤 영역에 키보드로 접근 불가
- **실제:** axe `scrollable-region-focusable`(serious)이 40조합 모두에서 나왔다. 지표 띠·표 감싸개·코드 블록이다.
- **원인:** [Shell.tsx:78](../../web/src/components/Shell.tsx), [DataTable.tsx:36](../../web/src/components/DataTable.tsx), `pre` 들
- **수정:** `tabIndex=0` + 이름(`<section aria-label>`)
- **커밋:** b57be8f → dd6ce70 (+ d1ec95f: 이 수정이 깨뜨린 Biome 린트를 고침 — 수정 커밋 뒤 린트를 돌리지 않아 놓쳤다)

### QA-013 (보통, UX·성능) 화면이 크게 밀림 (CLS)
- **재현:** `qa/probes/lighthouse.sh <폴더> --assert-cls`
- **실제:** 10조합 모두 CLS > 0.1 이다(0.21~0.83, Core Web Vitals 기준 '나쁨' 0.25 초과 다수).
- **원인(레이아웃 이동 기록으로 확인):**
  - [Shell.tsx:75](../../web/src/components/Shell.tsx) 지표 띠가 데이터 전 `null` → 본문 전체를 밂
  - [TradesPage.tsx:73·80](../../web/src/pages/TradesPage.tsx) 요약 줄·기간 선택기가 늦게 끼어듦
  - [QualityPage.tsx:113](../../web/src/pages/QualityPage.tsx) 높이 고정 골격
  - [RegionPicker.tsx:49](../../web/src/components/RegionPicker.tsx) 좁은 화면 줄바꿈 변화
- **수정:** 같은 구조로 자리를 먼저 그린다.
  - 처음 수정(694bfa3)은 거래 목록에서 부족했다. '불러오는 중'에만 자리를 뒀는데, URL 에 기간이 없으면 시세 띠가 올 때까지 불러오기가 시작되지 않는다. 독립 검토가 6번 중 3번 0.13~0.19 를 쟀다.
  - 첫 결과 전을 불러오는 중과 같게 그리도록 보완했다(ae05812). 거래 목록 반복 8회 최대 0.672 → 0.039, Lighthouse 10조합 × 2회 CLS 0.000~0.048, 성능 점수 68~89 → 90~100.
- **증거:** [qa-013-cls-before.txt](evidence/qa-013-cls-before.txt) → [qa-013-cls-after.txt](evidence/qa-013-cls-after.txt), [qa-013-trades-cls-before.txt](evidence/qa-013-trades-cls-before.txt) → [qa-013-trades-cls-after.txt](evidence/qa-013-trades-cls-after.txt), [qa-013-014-lighthouse-final.txt](evidence/qa-013-014-lighthouse-final.txt)
- **시험:** `qa/probes/lighthouse.sh --assert-cls`, `qa/probes/cls_trades.mjs`, 화면 시험 'QA-013 거래 목록'. 커밋: eda45c6 → 694bfa3, 다시 열어 02bd31e → ae05812

### QA-015 (보통, 기능) 지역 분석에서 긴 기간 → 422
- **재현:** QA 웹 `#/region/11110` 에서 '전체'를 누르거나 5년 근처 기간을 고른다.
- **실제:** `months?from=2021-01&to=2026-09` 가 422 RANGE_EXCEEDS_PLAN 이고, 화면에 오류가 나며, 같은 요청이 한 번 더 나간다.
- **원인:** [region.ts:19](../../web/src/domain/region.ts) 전년 대비용 12개월(`fetchFrom`)이 웹 플랜 상한(60개월)을 넘는다.
- **수정:** 받는 기간을 60개월 안으로 묶는다.
- **증거:** [qa-ui-interact-summary.txt](evidence/qa-ui-interact-summary.txt) → [qa-ui-interact-after-summary.txt](evidence/qa-ui-interact-after-summary.txt)
- **시험:** `region.test.ts` QA-015 4건(넷째 '여유가 있으면 12개월 그대로'는 회귀 방지용). 커밋: 5a8fe5f → d369475

### QA-003 (낮음) 유니코드 숫자 통과
- **재현:** `GET /v1/regions/41135/months?from=２０２４-01&to=2024-06` (전각)
- **실제:** 200 이고 2024-01 데이터를 준다(`int()` 가 전각 숫자를 읽음). 시군구 `١١١١٠` 은 빈 200 이다.
- **원인:** [params.py:9-10](../../api/src/aptlake_api/core/params.py) 등 6개 파일 8곳의 `\d` (params 2, admin 2, exports·index·quality·trades 각 1)
- **수정:** `[0-9]`
- **시험:** `test_qa_003_*` 7건. 그중 `/v1/regions/１１１１０/months` 는 수정 전에도 400(INVALID_REGION)이었고 오류 코드만 달랐다. 커밋: fd9204b → 368692c

### QA-004 (낮음, 보안) /docs 외부 스크립트 고정·SRI 없음
- **재현:** `curl -s http://127.0.0.1:3710/docs`
- **실제:** `swagger-ui-dist@5/swagger-ui-bundle.js` 를 integrity 없이 불러온다. 같은 출처라 BFF 가 붙이는 웹 키 권한으로 API 를 부를 수 있다.
- **원인:** [main.py:90](../../api/src/aptlake_api/main.py) FastAPI 기본 `/docs`
- **수정:** 5.33.1 고정 + sha384 SRI. 실제 Chrome 에서 화면이 그려짐을 확인했다(작업 25개, 콘솔 오류 0).
- **증거:** [qa-s4-docs-cdn.txt](evidence/qa-s4-docs-cdn.txt) → [qa-004-docs-after.txt](evidence/qa-004-docs-after.txt)
- **커밋:** a8d1548 → 0173209

### QA-012 (낮음, 성능) 시세 띠 중복 요청
- **실제:** 40조합 모두 `/v1/market/ticker ×2` 다(App·Shell 이 동시에 부르고, 캐시는 끝난 응답만 저장).
- **원인:** [client.ts:48](../../web/src/api/client.ts)
- **수정:** 진행 중인 같은 조회를 공유한다. 호출자별 취소는 그대로다.
- **시험:** `web/src/api/client.test.ts` QA-012 2건(둘째 '한 호출자 취소'는 회귀 방지용). 커밋: b57be8f → d393c98

### QA-014 (낮음, 접근성) 로고 링크·테마 버튼 이름 불일치 (WCAG 2.5.3)
- **실제:** 로고는 화면에 'AptLake 아파트 실거래 데이터'인데 이름이 'AptLake 홈'이다(Lighthouse `label-content-name-mismatch`). 테마 버튼도 보이는 '자동'이 이름에 없다.
- **원인:** [Shell.tsx:99·104](../../web/src/components/Shell.tsx)
- **수정:**
  - 처음 수정(8024770)은 이름을 'AptLake 아파트 실거래 데이터 · 홈'으로 바꿨지만 고쳐지지 않았다. 보이는 글자가 DOM 에서 공백 없이 'AptLake아파트…'로 붙어 있었고, 제 시험은 두 조각을 따로 봐서 이를 놓쳤다(독립 검토가 찾음).
  - 시험을 감사 방식(보이는 글자를 이어 붙여 공백만 정리)으로 강화했다. 로고는 aria-label 을 빼 보이는 글자에서 이름이 계산되게 했고, 테마 버튼은 이름을 보이는 글자로 시작하게 했다. Lighthouse 10조합 × 2회 모두 통과.
- **시험:** 화면 시험 'QA-014' 2건. 커밋: eda45c6 → 8024770, 다시 열어 6c54dd2 → ae05812

### QA-016 (낮음, 접근성) 팝업 Esc 뒤 초점 소실
- **재현:** 지역 선택 버튼에 초점 → Enter → Esc
- **실제:** 초점이 `body` 로 간다.
- **원인:** [ui.tsx:193](../../web/src/components/ui.tsx) Esc 처리
- **수정:** 연 버튼으로 초점을 되돌린다(지역·월·기간 선택 공통).
- **증거:** [qa-ui-keyboard.txt](evidence/qa-ui-keyboard.txt) → [qa-ui-keyboard-after.txt](evidence/qa-ui-keyboard-after.txt)
- **커밋:** 28ca113 → d8e6647

## 5. 확인됨 (결함 아님)
- **다른 클라이언트의 데이터·관리 경로**
  - 다른 클라이언트의 내보내기 작업은 404, 사용량은 자기 것만 보인다.
  - 관리 경로 변형 20종(대소문자·`//`·`%2F`·`..` 등)은 모두 차단됐다(생성된 클라이언트 0).
- **인젝션·XSS·CSP**
  - SQL·LIKE 인젝션 없음. 검색은 바인딩 + `positionCaseInsensitiveUTF8` 를 쓴다.
  - XSS: 조작 문자열이 든 단지·지역·로그를 8화면에서 열어 봤고 실행 0, CSP 위반 0이었다. 툴팁 formatter 8곳 모두 `esc()` 를 거친다.
- **헤더·CSRF·한도**
  - 보안 헤더(CSP·XFO·nosniff·Referrer·Permissions·COOP·CORP)가 웹·API 모두 있다.
  - CORS 사전 요청은 405, 교차 출처 POST 는 웹 키가 붙지 않아 403 이다.
  - XFF 위조는 무시되고(익명 20회 뒤 429), 동시 120건 중 정확히 60건이 통과했으며(free 분당 60), 인증 실패 30회 뒤 429 다(정상 키에는 영향 없음).
- **비밀값**
  - gitleaks 0, 웹 번들·이미지 설정·로그에 키 0이다.
  - 오류 로그의 `serviceKey=`·`password=` 는 가려진다.
- **EOL·의존성:** 지원 종료 0건(9개 이미지), pip-audit·npm audit 0건.
- **데이터 정확성**
  - 970건 일치: 지역 통계·커서로 끝까지 넘긴 거래 목록(빠짐·중복·순서)·분포 합계·단지 순위·시장 개요
  - 버전이 바뀌면 캐시가 무효화된다(프로세스 내 1초 캐시는 설계).
- **발행 원자성:** 표 교체 도중 끊기면 앞 두 표만 새 버전이 되는 창이 있다(ADR-038). 다시 발행하면 네 표가 맞춰지고 스테이징이 비워진다(새 시험).
- **백업·복원**
  - QA 스택에서 `make backup` 과 같은 스크립트 → `backup-verify` 가 모두 OK 였다.
  - 새 장비 복원 훈련(`restore_drill.sh`)에서 행 수가 같고 역할 권한도 유지됐다(api_app 읽기·갱신 가능, 삭제 거부). MinIO 객체도 모두 돌아왔다.
- **같은 요청 동시 처리**
  - 내보내기 동시 12건 → 2건만 접수(클라이언트 상한 2, advisory lock)
  - 로그 비우기·되돌리기 동시 40건 → 감사 기록 40건, 상태 일관
- **설계대로인 것들**
  - Redis 장애는 2초에 503 이고, 풀리면 바로 회복한다.
  - 작업자 재시작으로 끊긴 내보내기는 30분 뒤 실패로 정리된다.
  - API 재시작 중에는 약 2~3초 오류(직접 호출은 끊김, 웹 경유는 502)가 난다. 인스턴스가 하나이기 때문이다.
  - 수집 상태의 '잘못된 매개변수(to=2000-13)'·'잘못된 키' 요청은 연결 테스트가 일부러 보내는 부정 검사다.
- **키보드:** 초점 표시가 없는 요소 0(9화면 × Tab 40회). 검색 자동완성·지역 선택·월 이동이 키보드만으로 된다.
- **기타:** 개발자 화면 키 입력은 빈 키 무시, 형식 오류 401 안내, 유효 키는 사용량 표시이고 localStorage 에 남지 않는다.

## 6. 측정한 성능 (QA 스택, 생성 데이터 270만 건, 운영 스택이 같은 VM 에서 대기 중)
| 측정 | 결과 |
|---|---|
| 200+100 RPS (목표) | 처리량 261.5·286.4 req/s · months p95 2,045·1,489 ms · trades p95 3,467·2,432 ms (NFR-04 미달) |
| 80+40 RPS | 120.8 req/s · months p95 199·87 ms · trades p95 311·164 ms |
| 혼합 8경로 × 10 rps | 분포 p95 756~1,049 · 단지 상세 624~895 · 단지 목록 467~684 · 시장 개요 274~459 · 검색 235~306 · 지수 27~43 · 경계 32~43 · 시세 띠 26~29 ms |
| 느린 질의 (CPU 합) | region_month 19,240회 113 s(질의당 5.9 ms), 거래 페이지 68.6 s, 거래 요약 54.8 s · 단지 상세 질의당 47 ms(51천 행) · 검색 89천 행 읽음 |
| 응답·전송 크기 | 차트 묶음 676 KB(gzip 223 KB) · 화면 전송량 106~344 KiB · 전국 전체 내보내기 Parquet 90 MiB |
| 대량 내보내기 (수정 후) | 270만 행 ClickHouse 최대 216 MB · 3.9초, api-internal 최대 약 367 MiB / 512 MiB |
| Lighthouse (수정 후, 10조합 × 2회) | 성능 데스크톱 100 · 모바일 90~99, 접근성 100, 모범 사례 100, CLS 0.000~0.048, 모바일 LCP 1.7~3.3 s · 거래 목록 반복 8회 최대 CLS 0.039 |
| 장애 주입 (수정 후) | Redis 멈춤 → 2초 503 · ClickHouse 15초 멈춤 → 최대 16.4초(수정 전 15.9초), 40초 멈춤 → 최대 16.5초 · PG 잠금 → 약 2초 503 |

## 7. 검증하지 못한 부분과 이유
- **공개 배포 구성:** TLS·HSTS·리버스 프록시는 스택에 없다(모든 포트 127.0.0.1).
- **dbt 데이터 시험 22개:** 레이크에 수집 데이터가 있어야 해서 `dbt parse` 만 했다. QA 레이크에서는 SCD2 통합 시험만 돌렸다.
- **원천 API 실제 호출·장애:** 규칙상 부르지 않았다. 기존 단위 시험(저장 응답·목: 429·5xx·인증 거부)으로 대신했다.
- **Dagster 실행 도중 재시작·FETCHING 고착 복구:** QA 스택에 Dagster 를 띄우지 않았다(메모리). 미확인이다.
- **볼륨 손상 복원(QA 볼륨 삭제):** 볼륨 삭제가 권한 확인에서 거부됐다. 대신 새 컨테이너 복원 훈련으로 같은 절차를 검증했다.
- **스크린 리더·실제 모바일 기기:** 에뮬레이션·axe·키보드만 확인했다.
- **일일 행 한도의 동시 초과 여부, 키 존재 여부의 응답 시간 차이:** 시험하지 않았다(미확인).
- **성능 수치:** 전용 장비가 아닌 공유 VM(CPU 4)에서 쟀다. 운영 스택이 같은 VM 에서 대기 중이었다.

## 8. 출시 가능 여부
- **로컬 단일 노드 운영(현재 배포 형태): 출시 가능** — 이 브랜치를 배포한다는 조건이다. 근거는 다음과 같다.
  - 치명 결함 0
  - 높음 1건(QA-001 권한 상승) 수정·검증, 보통인 대량 내보내기 실패(QA-006)도 수정
  - 보안·신뢰성 핵심 경로(권한·인젝션·XSS·한도·비밀값·백업 복원·장애 시 응답 상한) 확인
  - 남은 미수정 2건: QA-005 는 닿는 경로가 확인되지 않은 패키지 취약점, QA-009 는 이미 공개된 성능 한계
- **공개 인터넷 서비스: 조건부.**
  1. TLS 종단과 HSTS
  2. ~~QA-005 이미지 갱신과 CI 이미지 스캔~~ — 후속 수정(13장)
  3. 기대 부하가 약 120 RPS 를 넘으면 QA-009 해소: CPU 증설 등 (같은 요청 합치기는 후속 반영, CPU 분리는 효과 없음 — 14장)
- **배포 절차** (운영 스택에는 하지 않았다):
  1. `docker compose up -d --build api api-internal web` 을 실행한다.
     - db-migrate 가 V008 을 적용하고, provision 이 웹 키를 `read`·`ops_read` 로 바꾼다.
     - Redis 키 캐시가 최대 30초 남는다.
  2. `infra/clickhouse/users.d` 는 ClickHouse 가 자동으로 다시 읽는다. 체크아웃하는 순간 exporter 프로필이 바뀐다.
  3. 파이프라인 코드는 바뀌지 않았다(시험만 추가). 재배포는 필요 없다.

## 9. 수정 우선순위 제안 (남은 일)
1. 이 브랜치 배포 (8장 절차).
2. ~~QA-005: 기반 이미지 다이제스트 갱신·재빌드, CI 에 이미지 Trivy 스캔 추가(닿지 않는 항목은 근거와 함께 예외), `psycopg[c]` 전환 검토.~~ 후속 수정(13장).
3. ~~QA-009: 같은 요청 합치기(A2), ClickHouse CPU 분리, API 워커·VM CPU 조정 뒤 같은 시나리오로 재측정.~~ 후속(14장): 합치기·월 계열 캐시는 채택, CPU 분리·연결·스레드 조정은 효과 없음, 워커 수는 미실험. VM CPU 는 사용자 결정.
4. 개선 제안 A3~A8 (아래).

## 10. 개선 제안 (결함 아님 — 구조·운영)
- **A1 기능 사이 직접 의존 2곳:** `complexes → trades`(`TRADE_COLS`·`trade_item`), `ops → quality`(`RESOLVED`). 공유 모듈로 옮기면 경계가 분명해진다. 순환 import 0, core → features 역방향 0.
- **A2 데이터 경로에 같은 요청 합치기 없음:** 캐시가 빈 상태에서 같은 분포 요청 50건 → ClickHouse 질의 250회. 경계(geo)에만 single-flight 가 있다. 발행 직후 캐시가 비는 순간 부하가 몰린다. → 후속 반영(14장, 250 → 20회).
- **A3 내보내기 임시 파일이 tmpfs(메모리):** 전체 기간 90 MiB 일 때 api-internal 최대 367/512 MiB. 데이터가 늘면 메모리 상한에 닿는다. 디스크 임시 폴더나 분할 업로드를 권한다.
- **A4 작업자 재시작 뒤 남은 `running` 작업:** 작업자가 하나뿐이므로 시작할 때 바로 정리할 수 있다(지금은 30분 뒤, 그동안 클라이언트 상한 1칸을 차지).
- **A5 재배포 중 2~3초 오류:** 인스턴스가 하나다. 웹 BFF 가 멱등 GET 을 다시 시도하게 하거나 순차 교체를 권한다.
- **A6 사용량 이벤트:** ClickHouse 장애 중에는 설계상 버린다(개수만 셈). 짧은 재시도 뒤 버리면 일시 장애에서 덜 잃는다.
- **A7 로그 비우기 감사 기록:** client_ip 가 비어 있다(관리 API 는 남김). 웹 경유는 actor 에 IP HMAC 이 있다.
- **A8 개발자 화면 키:** 다른 메뉴에 갔다 와도 입력란에 남는다. 안내 문구는 '이 화면 메모리에만'이다. localStorage 에는 없다.
- **A9 region_month 인덱스 간격 축소:** 읽는 행은 91% 줄지만 질의당 CPU 는 같다(4.1~4.7 ms). 권하지 않는다.
- **A10 ClickHouse 질의 전체 시간 상한:** 응답 상한(10초)은 소켓 대기에만 걸린다. 연결 풀이 모두 매달리면 빈 연결을 기다리는 시간이 더해져 15초 안팎이 된다. 질의 단위로 `asyncio.timeout` 을 두면 기한을 더 짧게 묶을 수 있다.

## 11. 리뷰
- 처음 보는 리뷰어 역할의 독립 에이전트가 이 보고서의 재현 절차와 심각도를 확인했다.
  - 실패 시험 커밋에서 실패, 수정 커밋·HEAD 에서 통과하는지 결함마다 봤다.
  - 바로 앞 수정 커밋에서 아직 실패하는지도 확인했다.
  - QA 스택에서 BFF·화면·Lighthouse 점검을 다시 돌렸다.
- 운영 스택에는 요청도 docker 조작도 하지 않았다(메모리 여유를 본 `docker stats` 한 번 제외).

## 12. 리뷰 결과와 반영
| 지적 | 반영 |
|---|---|
| QA-014 가 실제로는 고쳐지지 않음 (HEAD 에서도 Lighthouse 감사 10조합 실패, 테마 버튼도 같은 문제) | 시험을 감사 방식으로 강화(6c54dd2, 실패) → 로고 aria-label 제거·테마 버튼 이름 수정(ae05812) → Lighthouse 10조합 × 2회 통과 |
| QA-013 거래 목록이 간헐적으로 CLS 0.13~0.19 — 1회 측정값을 결과로 적음 | 원인(첫 결과 전 자리 없음)을 반복 추적으로 찾아 실패 시험(02bd31e) → 수정(ae05812). 반복 8회·Lighthouse 2회 측정값으로 바꿈 |
| QA-006 은 우회 가능한 경계 실패라 보통이 맞음, '5년 373MB' 증거 없음 | 보통으로 낮춤, query_log 기록(373·395 MB)을 증거에 덧붙임 |
| QA-007 '5초 근처 503' 미달성, 15초 멈춤 최대값은 줄지 않음 | 기대·실제를 그대로 고쳐 씀, 질의 단위 전체 상한을 개선 제안(A10)으로 |
| QA-009 '120 RPS 는 근처' 는 과장 | '120 RPS 에서도 미달'로 고침 |
| QA-001 API 실패 시험은 결함을 직접 재현하지 않음, ADR-032 와의 충돌 | 재현 근거(BFF 점검 전·후)와 근거의 충돌을 적음 |
| QA-003 '7곳' → 6개 파일 8곳, 1건은 수정 전에도 400 | 고침 |
| 재현 시험 수, 회귀 방지용 시험 표시, 단위 시험 항목 누락 | 고침 (결함 재현 34개, 회귀 방지용 2개 표시) |
| QA-002 연결 끊김 인과·QA-008 풀 고갈은 정황·추정 | '정황'·'측정하지 않은 추정'으로 표시 |

리뷰가 확인하지 못한 것: QA-005 Trivy 재스캔(도구 없음), QA-009 재측정(운영과 같은 VM).

## 13. 후속 수정 — QA-005 (출시 뒤, 브랜치 `improve/qa-005-image-vulns`)
결정과 대안은 [ADR-040](../decisions.md). 증거: [qa-005-scan-images.txt](evidence/qa-005-scan-images.txt), [qa-005-psycopg-impl-latency.txt](evidence/qa-005-psycopg-impl-latency.txt), [qa-005-pipeline-image-check.txt](evidence/qa-005-pipeline-image-check.txt).

| 대상 | 수정 전 | 수정 | 수정 후 |
|---|---|---|---|
| api 이미지 | CRITICAL 3·HIGH 6 | 빌드 때 OS 보안 갱신 + `psycopg-c`(시스템 libpq) 다단계 빌드 | 0 |
| pipeline 이미지 | CRITICAL 3·HIGH 6 | 같음 + psycopg2 소스 빌드(dagster-postgres) | 0 |
| web 이미지 | HIGH 1 | 빌드 때 `apk upgrade` | 0 |
| 서드파티 9종 | 예: trino 31·postgres 22·silo 10·grafana 5·redis 4·prometheus 2·clickhouse 1 | Grafana·Silo 패치 태그, 나머지는 이미지별 예외(이유·만료일) | 예외 뒤 0 |
| CI | 소스 트리만 스캔 | 이미지 3종을 `--pull` 로 빌드해 스캔(커밋마다), 서드파티는 주 1회·compose PR | — |

- **동작 확인 (QA 스택):**
  - 레이크 통합 시험 2개 통과(새 파이프라인 이미지·새 Silo)
  - dagster-postgres 실행 저장소 초기화(psycopg2), Dagster 정의 검증
  - 대량 내보내기(새 Silo): 716행 Parquet, 서명 URL 다운로드 200
  - Grafana 12.4.12 프로비저닝(대시보드 2·데이터 소스 1)
- **성능 (PG 를 많이 쓰는 경로 p50, 120회):** binary 3.6 · C 3.5~3.9 · 순수 파이썬 6.0 ms(품질 격자). 순수 파이썬은 1~6 ms 느려 쓰지 않았다.
- **예외 파일 검증:** 예외를 빼면 실패한다(Redis 4·Trino 31건). 만료일이 지난 예외는 적용되지 않는다. CI 와 같은 amd64 에서도 경로가 맞는다.
- **확인하지 못한 것:**
  - 운영 스택 재배포 뒤 스캔: 배포는 사용자가 한다.
  - amd64 에서의 psycopg-c 빌드: CI 실행으로 확인한다.

## 14. 후속 수정 — QA-009 (출시 뒤, 브랜치 `improve/qa-009-performance`)
결정과 대안은 [ADR-041](../decisions.md). 증거: [qa-009-analysis.txt](evidence/qa-009-analysis.txt)(원인 분석·설정 실험), [qa-009-ab.txt](evidence/qa-009-ab.txt)(수정 전·후 교차), [qa-009-stampede.txt](evidence/qa-009-stampede.txt)(같은 요청 몰림).

- **권고를 측정으로 가렸다** (200+100 RPS, 기준 설정 6회: 처리량 253~283 req/s · months p95 1,431~2,468 ms · trades p95 2,271~4,013 ms):
  - 효과 없음: ClickHouse 질의 스레드 2 → 1, 워커당 연결 20 → 4, CPU 고정 분리(cpuset), API CPU 가중치. 지연은 모두 기준 범위 안이었다(cpuset 분리의 처리량 286 은 기준 최대보다 3 높지만 기준 회차끼리의 차이 30 보다 작다).
  - API 워커 수는 바꿔 보지 않았다. 워커마다 CPU 22~25% 로 포화가 아니어서 미뤘다(효과 **미확인**). VM CPU 는 사용자 설정이라 바꾸지 않았다.
  - VM 은 평균 약 20% 비어 있었지만 CPU 압박(PSI)이 VM 70.9~76.4%·API 34.7~36.7% 였다. 캐시 적중 응답도 p95 450~900 ms 였다.
- **원인:**
  1. 캐시가 빈 직후의 몰림: 같은 요청도 각자 계산했다(A2). months 는 무작위 기간이라 결과 캐시 적중률이 42~45% 였고, ClickHouse 질의의 절반이 region_month 였다.
  2. 측정 환경: dockerd 가 약 10초마다 CPU 를 크게 쓰고(부하 없을 때도) 그 초에 요청이 1초 안팎 밀린다. 무엇이 dockerd 를 부르는지는 **미확인**이다.
     80+40 RPS(수정 전)에서 첫 3초와 이 초들을 빼면 months p95 26 ms · trades p95 49 ms 로 목표 안이다.
- **실패 시험 → 수정:**
  - `test_qa_009_concurrent_identical_requests_compute_once`: 같은 요청 동시 30건이 30번 계산 → 1번 (single-flight)
  - `test_qa_009_months_windows_share_one_series_query`: 기간만 다른 4건이 질의 4회 → 1회 이하 (데이터셋 버전별 월 계열 캐시)
  - `test_qa_009_months_series_cache_follows_dataset_version`: 버전이 바뀐 직후 기간이 다른 12건이 동시에 와도 표 읽기 1회, 버전마다 다시 읽는다 (수정 전 코드에서는 요청마다 읽어 11~12회로 실패)
  - `test_qa_009_months_series_cache_expires`: 버전이 그대로여도 TTL 이 지나면 다시 읽는다 (독립 검토 지적으로 추가)
  - 합치기 단위 시험 2개: 첫 요청이 끊겨도 기다리던 요청이 받는다, 실패가 모두에 전해지고 키가 풀린다
  - 읽기 직후 다시 읽던 틈(워커 4개에 5~7회)은 실행 순서에 달려 있어 시험으로 재현하지 못했다. QA 스택에서 API 를 새로 띄운 직후 months 400건 동시에 4회로 확인했다.
- **결과** (수정 전·후 이미지를 번갈아 3쌍, 수정 후는 계열 캐시까지 빈 상태 — 발행 직후와 같은 조건. 수정 후 이미지는 위의 틈·TTL 을 고치기 전 코드):

| 200+100 RPS, 60초 | 수정 전 (3회) | 수정 후 (3회) |
|---|---|---|
| 처리량 (months+trades) | 237~253 req/s | 243~288 req/s |
| months p50 / p95 | 209~527 / 2,318~3,260 ms | 14~102 / 753~2,264 ms |
| trades p50 / p95 | 326~1,081 / 4,014~4,871 ms | 52~236 / 1,461~4,533 ms |
| ClickHouse 질의 · CPU 합 | 11,184~11,552 회 · 72~77 s | 5,482~6,423 회 · 47 s |
| 부하 중 CPU (API · ClickHouse) | 94~99 · 144~153 % | 86~88 · 96 % |
| 같은 분포 요청 50건 몰림 | 질의 250회 | 20회 (워커 4 × 요청당 5) |

  - 세 쌍 모두 지연(p50·p95)은 수정 후가 앞섰다. 처리량은 첫 쌍만 수정 후가 조금 낮았다.
  - 결과 캐시 적중률은 그대로(months 42~46%)이고, 줄어든 것은 미적중 한 건의 비용이다.
  - 응답은 바뀌지 않았다(공개 응답 골든 36개 그대로). 계열 캐시는 워커당 약 4.1 MiB 다.
  - 최종 코드로 80+40 RPS 1회(API 새로 띄운 직후): 첫 3초·dockerd 급증 초를 빼면 months p95 28 ms · trades p95 84 ms 다.
- **독립 검토:** 처음 보는 리뷰어 역할의 에이전트가 코드·시험·문서 수치를 대조했다. 반영한 것:
  - 몰림 시험이 캐시 키를 잘못 지워 시험 순서에 따라 결과가 달랐다 → 경로 이름 수정
  - 증거에 없던 기준선 2회, '수정 후 months 0회', 워커 수 '효과 없음'(실험하지 않음), 버전 시험 설명, 적중률 '거의 0' 표현 → 증거를 보태고 문장을 고침
  - CPU 평균을 부하 60초가 아니라 약 85초로 내 25~30% 낮게 적었다 → 부하 구간으로 다시 계산, `timeline.sh` 도 고침
  - 계열 캐시 TTL 없음 → 10분 TTL
  - `timeline.sh` 의 중단 시 정리 → trap 추가
  - 클라이언트 사이 결과 공유·취소·예외·ETag·행 차감·출력 동일성은 문제없음으로 확인했다
- **판단:** NFR-04(p95 80·150 ms @ 200+100 RPS)는 이 환경에서 **아직 미달**이다. QA-009 는 '일부 수정'으로 둔다.
- **다음 수단(사용자 결정 필요):** 도커 VM CPU 늘리기(다른 프로젝트와 공유), 전용 측정 환경, 목표값을 로컬 환경에 맞게 조정.
- **시험:** 348 → **354개** (API 164 → 170), 전부 통과.
- **확인하지 못한 것:**
  - dockerd 10초 주기의 원인(무엇이 부르는지)
  - API 워커 수·VM CPU 를 바꿨을 때의 효과
  - 운영 스택 재배포 뒤 측정 — 운영에는 부하를 주지 않는다
