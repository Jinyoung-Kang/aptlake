"""환경변수 기반 설정. 비밀값은 repr 에 나오지 않도록 SecretStr 처럼 다룬다."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


class Secret(str):
    def __repr__(self) -> str:  # 로그·예외 메시지로 새지 않게
        return "Secret('***')"


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, default)
    if v is None:
        raise RuntimeError(f"missing environment variable {name}")
    return v


@dataclass(frozen=True)
class Settings:
    data_go_kr_key: Secret = field(repr=False)
    reb_api_key: Secret = field(repr=False)
    vworld_api_key: Secret = field(repr=False)
    vworld_domain: str
    pg_dsn: Secret = field(repr=False)
    redis_url: Secret = field(repr=False)
    catalog_uri: str
    catalog_warehouse: str
    trino_host: str
    trino_port: int
    s3_endpoint: str
    s3_ingest_access_key: str
    s3_ingest_secret_key: Secret = field(repr=False)
    ch_host: str
    ch_port: int
    ch_publisher_password: Secret = field(repr=False)
    rtms_daily_limit: int
    rtms_budget_pct: int
    backfill_from: str
    provisional_days: int
    fetch_concurrency: int
    fetch_tps: float

    @property
    def rtms_daily_cap(self) -> int:
        return self.rtms_daily_limit * self.rtms_budget_pct // 100


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        data_go_kr_key=Secret(_env("DATA_GO_KR_KEY", "")),
        reb_api_key=Secret(_env("REB_API_KEY", "")),
        vworld_api_key=Secret(_env("VWORLD_API_KEY", "")),
        vworld_domain=_env("VWORLD_DOMAIN", ""),
        pg_dsn=Secret(_env("PG_DSN")),
        redis_url=Secret(_env("REDIS_URL")),
        catalog_uri=_env("CATALOG_URI", "http://lakekeeper:8181/catalog"),
        catalog_warehouse=_env("CATALOG_WAREHOUSE", "aptlake"),
        trino_host=_env("TRINO_HOST", "trino"),
        trino_port=int(_env("TRINO_PORT", "8080")),
        s3_endpoint=_env("S3_ENDPOINT", "http://minio:9000"),
        s3_ingest_access_key=_env("S3_INGEST_ACCESS_KEY", "ingest"),
        s3_ingest_secret_key=Secret(_env("S3_INGEST_SECRET_KEY", "")),
        ch_host=_env("CH_HOST", "clickhouse"),
        ch_port=int(_env("CH_PORT", "8123")),
        ch_publisher_password=Secret(_env("CH_PUBLISHER_PASSWORD", "")),
        rtms_daily_limit=int(_env("RTMS_DAILY_LIMIT", "10000")),
        rtms_budget_pct=int(_env("RTMS_BUDGET_PCT", "80")),
        backfill_from=_env("BACKFILL_FROM", "2021-01").replace("-", ""),
        provisional_days=int(_env("PROVISIONAL_DAYS", "60")),
        # 기획서 10장: 파티션 동시 4, 초당 호출 ≤ 10 (명세 30 tps 의 1/3)
        fetch_concurrency=int(_env("FETCH_CONCURRENCY", "4")),
        fetch_tps=float(_env("FETCH_TPS", "10")),
    )
