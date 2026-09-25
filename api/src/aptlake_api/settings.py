"""API 설정 (환경변수). 비밀값은 SecretStr 로 두어 로그·repr 에 나오지 않게 한다."""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    pg_dsn: SecretStr
    redis_url: SecretStr
    ch_host: str = "clickhouse"
    ch_port: int = 8123
    ch_reader_password: SecretStr
    ch_usage_password: SecretStr
    ch_export_password: SecretStr = SecretStr("")
    api_key_pepper: SecretStr
    cursor_signing_key: SecretStr
    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str = "http://127.0.0.1:9600"
    s3_export_access_key: str = "export"
    s3_export_secret_key: SecretStr = SecretStr("")
    # 이 주소에서 온 요청만 X-Forwarded-For 를 믿는다 (웹 nginx 컨테이너 고정 IP)
    trusted_proxies: str = ""
    key_cache_ttl_s: int = 30  # FR-501: 폐기 반영 ≤ 30초 (폐기 시 즉시 삭제도 함)
    result_cache_ttl_s: int = 600  # 기획서 10장: 결과 캐시 TTL 10분 (키에 dataset_ver 포함)
    provisional_days: int = 60  # FR-403: 계약월 말일 + 60일 전이면 provisional
    usage_flush_interval_s: float = 1.0
    usage_flush_max: int = 1000
    export_url_ttl_s: int = 900
    metrics_port: int = 8612

    @property
    def trusted_networks(self) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
        return [ipaddress.ip_network(x.strip()) for x in self.trusted_proxies.split(",") if x.strip()]


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
