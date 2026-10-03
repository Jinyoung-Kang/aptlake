"""기능별 모듈. 기능마다 세 계층으로 나눈다 — 의존은 바깥(router·repository) → 업무 규칙(service) 한 방향.

router.py      HTTP: 경로·매개변수 검증, 스코프, 캐시·ETag(core.http.respond), 저장소를 만들어 service 에 넘긴다
service.py     업무 규칙: 순수 함수. FastAPI·DB 드라이버를 import 하지 않는다 (tests/test_architecture.py 가 검사)
repository.py  데이터 접근: SQL 만. service 가 정의한 Protocol 을 구현한다
"""
