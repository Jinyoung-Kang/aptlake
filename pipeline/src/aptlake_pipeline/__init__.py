"""패키지 공통 설정."""

import logging

# httpx·httpcore 는 INFO 수준에서 요청 URL 전체를 남긴다. 공공데이터 API 는 인증키가 URL 쿼리(serviceKey=·key=)에
# 들어가므로, 누가 로그 수준을 INFO 로 올려도 키가 로그·Dagster 이벤트에 남지 않도록 경고 이상만 남긴다.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
