# 성능 측정 기록

측정 전에는 README 에 성능 수치를 쓰지 않는다는 기획서 원칙(10장)에 따라, 아래는 모두 실제 측정값이다.

## 환경

- MacBook (Apple M1, 16GB), macOS 26.6, Docker Desktop VM: CPU 5 · 메모리 7.75GB
  - 같은 VM 에 다른 프로젝트 컨테이너가 약 3.8GB 를 쓰는 상태
- API: uvicorn 워커 4, ClickHouse 25.8 (`api_reader`: max_threads 2, readonly), Redis 7.4
- k6 1.3.0 을 **같은 도커 네트워크 안**에서 실행 (`http://api:8610`), 즉 호스트 포트 포워딩 비용은 제외하고 k6 자신도 같은 VM 의 CPU 를 씀
- 측정 중 수집 파이프라인 정지 (센서 멈춤, 실행 없음)
- 데이터: 측정 시점 반영된 계약월 8개 × 256 시군구 (서빙 테이블 약 38만 거래)

## API 부하 (2026-09-26 01:55 KST, 결과 캐시를 비운 뒤 60초)

시나리오 ([loadtest/api.js](../loadtest/api.js)): months 200 RPS(무작위 시군구·임의 12개월 창) + trades 100 RPS(무작위 시군구·임의 한 달, limit 200) + 익명 1 RPS(한도 초과 확인), 모두 constant-arrival-rate.

| 요청 | med (ms) | p90 | p95 | max |
|---|---|---|---|---|
| 지역·월 통계 (200 RPS) | 3.4 | 10.7 | 38.6 | 671.7 |
| 거래 목록 limit=200 (100 RPS) | 3.6 | 12.8 | 42.0 | 659.8 |
| 캐시 적중 요청 | 1.7 | 3.7 | 9.3 | 201.5 |
| 캐시 미스 요청 | 6.0 | 24.9 | 145.6 | 671.7 |

- 전체 18,033 요청 (299.9/s), 데이터 시나리오 HTTP 실패 0, k6 가 제때 시작하지 못한 반복 31건
- 결과 캐시 적중률 0.561 (측정 도중 채워짐)
- 익명 시나리오 429 비율 0.607 — 분당 20회 한도에 초당 1회 → 약 2/3 가 거절되는 것이 기대값, 모든 429 에 `Retry-After`
- 같은 1분 구간 ClickHouse 서버측 질의 시간(`system.query_log`, api_reader): p50 2 ms, p95 7~19 ms
  → 캐시 미스 p95 145 ms 의 대부분은 DB 가 아니라 API 워커·VM CPU 대기

기획서 목표(NFR-04): months p95 < 80 ms @ 200 RPS, trades(200행) p95 < 150 ms → 충족.

## 개선 과정

| 회차 | 변경 | months p95 | trades p95 | 달성 RPS | 실패 |
|---|---|---|---|---|---|
| 1 | 단일 워커, 요청당 Redis 왕복 약 8회, k6 는 호스트 경유 | 4,920 ms | 4,432 ms | 116 | 8.9 % |
| 2 | 대기형 Redis 풀(64), Lua 한 번에 한도+행 사용량, 프로세스 내 키 캐시 5초, 캐시 값 한 키, 워커 4, k6 네트워크 내부 | 915 ms | 723 ms | 292 | 0 (데이터) |
| 3 | 시군구 이름표 프로세스 캐시(데이터셋 버전별) → months 미스당 ClickHouse 질의 1회 절약 | **38.6 ms** | **42.0 ms** | 300 | 0 |

1회차 실패는 모두 `redis.exceptions.MaxConnectionsError` (연결 풀이 고갈되면 기다리지 않고 예외 → 500).
2회차의 긴 꼬리는 같은 구간 ClickHouse 질의 p95 가 112 ms 로 올라간 것과 겹친다 (months 미스마다 질의 2회).

## 파이프라인

| 작업 | 규모 | 시간 |
|---|---|---|
| 전국 한 달 (month_pipeline, 2024-08) | 256 호출, 45,980 거래 | 1분 45초 = bronze 47s · silver 17s · dbt build 16s · ClickHouse 발행 3.4s |
| 같은 달 재수집 (원본 해시 동일) | 256 호출 | bronze 에 0행 추가, silver·gold 건너뜀 |
| SCD2 통합 테스트 (MERGE 7회) | 합성 파티션 | 17초 |

## 정확성 대조

| 항목 | 결과 |
|---|---|
| gold 분위수 SQL vs numpy.percentile (종로 55건·분당 691건, 2024-07) | 최대 절대 차이 0.0 |
| Trino gold ↔ ClickHouse (발행마다) | 건수·합계 불일치 시 파티션 교체 안 함 (스테이징 대조) |

## 지수 산출
| 항목 | 값 |
|---|---|
| 입력 | 완결 33개월, 해제·이상치 제외 거래 (gold.trade_serving, PyIceberg 직접 읽기) |
| 추정 | 전국 + 시도 16 = 17개 모형 |
| 시간 · 최대 RSS | 12.8초 · 980MB (문자열 열을 사전 인코딩 정수로 처리한 뒤) |

## 메모리 (docker stats, lake 프로필, 수집 실행 직후)

Trino 1.48GB (단지 차원 전체 재생성 중 최대 1.8GiB, 한도 2GB) · ClickHouse 1.02GB · Dagster 0.72GB · MinIO 0.34GB · Postgres 94MB · API 80MB · Lakekeeper 42MB · Redis 11MB — 합계 약 3.8GB.
`make serve` (서빙만) 는 Trino·Dagster 를 띄우지 않는다.
