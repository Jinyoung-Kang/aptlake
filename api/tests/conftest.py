"""Testcontainers: PostgreSQL·Redis·ClickHouse 를 실제로 띄우고 운영과 같은 마이그레이션·사용자 설정을 쓴다."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import clickhouse_connect
import httpx
import psycopg
import pytest
import pytest_asyncio
from testcontainers.community.clickhouse import ClickHouseContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from aptlake_api.core import keys
from aptlake_api.core.settings import settings

ROOT = Path(__file__).resolve().parents[2]
PW = "test-pw"


@pytest.fixture(scope="session")
def stack():
    with (
        PostgresContainer("postgres:16-alpine", username="postgres", password=PW, dbname="aptlake") as pg,
        RedisContainer("redis:7.4-alpine") as rd,
        ClickHouseContainer("clickhouse/clickhouse-server:26.8", username="admin", password=PW)
        .with_env("CLICKHOUSE_READER_PASSWORD", PW)
        .with_env("CLICKHOUSE_USAGE_PASSWORD", PW)
        .with_env("CLICKHOUSE_PUBLISHER_PASSWORD", PW)
        .with_volume_mapping(
            str(ROOT / "infra/clickhouse/users.d/aptlake-users.xml"), "/etc/clickhouse-server/users.d/aptlake-users.xml"
        )
        # 운영과 같은 서버 설정 — 엔진 버전을 올렸을 때 없어진 설정 이름이 여기서 먼저 걸린다 (26.8 업그레이드 때 놓쳤던 것)
        .with_volume_mapping(
            str(ROOT / "infra/clickhouse/config.d/aptlake.xml"), "/etc/clickhouse-server/config.d/aptlake.xml"
        ) as ch,
    ):
        pg_host, pg_port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        su = f"postgresql://postgres:{PW}@{pg_host}:{pg_port}/aptlake"
        with psycopg.connect(su, autocommit=True) as c:
            for role in ("pipeline", "api_app", "migrator"):
                c.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{PW}'")
        os.environ["MIGRATOR_DSN"] = su
        os.environ["MIGRATIONS_DIR"] = str(ROOT / "api/migrations")
        from aptlake_api import migrate

        assert migrate.main() == 0

        ch_host, ch_port = ch.get_container_host_ip(), int(ch.get_exposed_port(8123))
        admin = clickhouse_connect.get_client(host=ch_host, port=ch_port, username="admin", password=PW)
        for stmt in (ROOT / "infra/clickhouse/init/01-schema.sql").read_text().split(";"):
            if stmt.strip():
                admin.command(stmt)
        admin = clickhouse_connect.get_client(
            host=ch_host, port=ch_port, username="admin", password=PW, database="aptlake"
        )
        _seed_clickhouse(admin)
        _seed_rent(admin)

        env = {
            "PG_DSN": f"postgresql://api_app:{PW}@{pg_host}:{pg_port}/aptlake",
            "REDIS_URL": f"redis://{rd.get_container_host_ip()}:{rd.get_exposed_port(6379)}/0",
            "CH_HOST": ch_host,
            "CH_PORT": str(ch_port),
            "CH_READER_PASSWORD": PW,
            "CH_USAGE_PASSWORD": PW,
            "API_KEY_PEPPER": "test-pepper",
            "CURSOR_SIGNING_KEY": "test-cursor-key",
            "METRICS_PORT": "0",
            "USAGE_FLUSH_INTERVAL_S": "0.2",
        }
        os.environ.update(env)
        yield {"su": su, "ch": admin, "ch_container": ch}


def _seed_clickhouse(c) -> None:
    c.insert(
        "region",
        [
            ["11110", "11", "서울특별시", "종로구", "서울특별시 종로구"],
            ["41135", "41", "경기도", "성남시 분당구", "경기도 성남시 분당구"],
        ],
        column_names=["sgg_cd", "sido_cd", "sido_nm", "sgg_nm", "full_nm"],
    )
    rows = []
    for m in range(1, 13):
        rows.append(
            ["41135", dt.date(2024, m, 1), 100 + m, 95 + m, 5, 90, 1, 1200.0, 1500.0 + m, 1800.0, 0, "gold@test.1"]
        )
    c.insert(
        "region_month",
        rows,
        column_names=[
            "sgg_cd",
            "month",
            "reported",
            "trades",
            "cancelled",
            "priced",
            "outliers",
            "p25_ppm2",
            "median_ppm2",
            "p75_ppm2",
            "low_sample",
            "dataset_ver",
        ],
    )
    # 전국('00')·시도('41') 월 집계 — 전국은 시군구 합보다 크게 (다른 시군구가 있다고 가정)
    roll = [
        [
            rid,
            level,
            dt.date(2024, m, 1),
            (100 + m) * k,
            (95 + m) * k,
            5 * k,
            90 * k,
            1,
            1200.0,
            1500.0 + m,
            1800.0,
            0,
            "gold@test.1",
        ]
        for rid, level, k in (("00", "nation", 10), ("41", "sido", 4))
        for m in range(1, 13)
    ]
    c.insert(
        "rollup_month",
        roll,
        column_names=[
            "region_id",
            "level",
            "month",
            "reported",
            "trades",
            "cancelled",
            "priced",
            "outliers",
            "p25_ppm2",
            "median_ppm2",
            "p75_ppm2",
            "low_sample",
            "dataset_ver",
        ],
    )
    # 자체 지수: 2023-01 = 100 부터 매달 +1
    c.insert(
        "price_index",
        [
            [
                "00",
                "HEDONIC_TD_v1",
                dt.date(2023 + i // 12, i % 12 + 1, 1),
                100.0 + i,
                99.0 + i,
                101.0 + i,
                1000,
                "test",
            ]
            for i in range(24)
        ],
        column_names=["region_id", "method", "period", "index_value", "ci_low", "ci_high", "n_obs", "model_ver"],
    )
    trades = []
    ts = dt.datetime(2024, 8, 1, tzinfo=dt.UTC)
    for i in range(250):
        day = dt.date(2024, 7, 1 + i % 31)
        trades.append(
            [
                f"41135-202407-{i:016x}-0",
                "41135",
                day,
                "c_" + "a" * 20,
                "테스트단지",
                "백현동",
                "1-1",
                Decimal("84.9"),
                5,
                100000 + i,
                1177.0,
                int(i % 10 == 0),
                None,
                None,
                "",
                "중개거래",
                "개인",
                "개인",
                2009,
                0,
                1,
                ts,
                None,
            ]
        )
    c.insert(
        "trade_current",
        trades,
        column_names=[
            "trade_id",
            "sgg_cd",
            "deal_date",
            "complex_key",
            "apt_nm",
            "umd_nm",
            "jibun",
            "area_m2",
            "floor",
            "price_manwon",
            "ppm2",
            "is_cancelled",
            "cancel_date",
            "registered_date",
            "apt_dong",
            "deal_kind",
            "seller_type",
            "buyer_type",
            "build_year",
            "is_outlier",
            "version",
            "valid_from",
            "missing_since",
        ],
    )
    c.insert(
        "trade_version",
        [
            [
                "41135-202407-0000000000000000-0",
                dt.date(2024, 7, 1),
                1,
                ts,
                ts + dt.timedelta(days=30),
                0,
                0,
                None,
                None,
                "",
                "중개거래",
                "개인",
                "개인",
            ],
            [
                "41135-202407-0000000000000000-0",
                dt.date(2024, 7, 1),
                2,
                ts + dt.timedelta(days=30),
                None,
                1,
                1,
                dt.date(2024, 8, 20),
                None,
                "",
                "중개거래",
                "개인",
                "개인",
            ],
        ],
        column_names=[
            "trade_id",
            "deal_date",
            "version",
            "valid_from",
            "valid_to",
            "is_current",
            "is_cancelled",
            "cancel_date",
            "registered_date",
            "apt_dong",
            "deal_kind",
            "seller_type",
            "buyer_type",
        ],
    )
    c.insert(
        "complex",
        [
            ["c_" + "a" * 20, "41135", "백현동", "1-1", "테스트단지", 2009, 0, dt.date(2024, 7, 1), 225],
            # 전월세에만 나온 단지 (ADR-050) — 토지임대부는 매매 원천에만 있는 항목이라 NULL(미상)로 발행된다
            ["c_" + "c" * 20, "41135", "정자동", "", "전월세단지", 1995, None, dt.date(2023, 10, 1), 0],
        ],
        column_names=[
            "complex_key",
            "sgg_cd",
            "umd_nm",
            "jibun",
            "apt_nm",
            "build_year",
            "land_leasehold",
            "first_seen",
            "trades",
        ],
    )


def _seed_rent(c) -> None:
    """전월세 서빙 표 (ADR-050): 분당구 시군구 월 통계 2024년, 테스트단지 계약 2024-05~07 (매매 2024-07 과 같은 단지)."""
    stat = ["contracts", "jeonse", "wolse", "renewals", "new_contracts", "rr_used", "jeonse_priced", "outliers",
            "jeonse_p25_ppm2", "jeonse_median_ppm2", "jeonse_p75_ppm2", "wolse_median_rent", "wolse_median_deposit",
            "low_sample", "dataset_ver"]  # fmt: skip
    c.insert(
        "region_rent_month",
        [["41135", dt.date(2024, m, 1), 200 + m, 110 + m, 90, 50, 140 + m, 10, 100 + m, 1, 600.0, 700.0 + m, 800.0,
          150.0, 5000.0, 0, "gold@test.1"] for m in range(1, 13)],
        column_names=["sgg_cd", "month", *stat],
    )  # fmt: skip
    c.insert(
        "rent_rollup_month",
        [[rid, level, dt.date(2024, m, 1), (200 + m) * k, (110 + m) * k, 90 * k, 50 * k, (140 + m) * k, 10 * k,
          (100 + m) * k, k, 600.0, 700.0 + m, 800.0, 150.0, 5000.0, 0, "gold@test.1"]
         for rid, level, k in (("00", "nation", 10), ("41", "sido", 4)) for m in range(1, 13)],
        column_names=["region_id", "level", "month", *stat],
    )  # fmt: skip
    ts = dt.datetime(2024, 8, 2, tzinfo=dt.UTC)
    rows = []
    for month, n in ((5, 20), (6, 20), (7, 60)):
        for i in range(n):
            jeonse = i < (40 if month == 7 else n)
            deposit = 60000 + i * 100 if jeonse else 5000
            rows.append(
                [f"r41135-2024{month:02d}-{i:016x}-0", "41135", dt.date(2024, month, 1 + i % 28), "c_" + "a" * 20,
                 "테스트단지", "백현동", "1-1", Decimal("84.9"), 3 + i % 15, deposit, 0 if jeonse else 150 + i,
                 int(jeonse), deposit / 84.9, "신규" if i % 3 else "갱신", "24.08~26.08",
                 "사용" if i % 10 == 0 else "", 55000 if i % 3 == 0 else None, None, 2009,
                 int(month == 7 and i == 39), 1, ts, None]
            )  # fmt: skip
    # 전월세에만 나온 단지의 계약 1건 — 단지 상세 창(2023-08~2024-07) 안, 다른 골든의 조회 범위(2024-03~) 밖
    rows.append(["r41135-202310-00000000000000cc-0", "41135", dt.date(2023, 10, 14), "c_" + "c" * 20, "전월세단지",
                 "정자동", "", Decimal("59.8"), 7, 30000, 0, 1, 30000 / 59.8, "신규", "23.11~25.11", "", None, None,
                 1995, 0, 1, ts, None])  # fmt: skip
    c.insert(
        "rent_current",
        rows,
        column_names=[
            "rent_id",
            "sgg_cd",
            "deal_date",
            "complex_key",
            "apt_nm",
            "umd_nm",
            "jibun",
            "area_m2",
            "floor",
            "deposit_manwon",
            "monthly_rent_manwon",
            "is_jeonse",
            "deposit_ppm2",
            "contract_type",
            "contract_term",
            "use_rr_right",
            "pre_deposit_manwon",
            "pre_monthly_rent_manwon",
            "build_year",
            "is_outlier",
            "version",
            "valid_from",
            "missing_since",
        ],  # fmt: skip
    )
    rid = "r41135-202407-0000000000000000-0"
    c.insert(
        "rent_version",
        [[rid, dt.date(2024, 7, 1), 1, ts, ts + dt.timedelta(days=20), 0, "", "", "", None, None, 2009],
         [rid, dt.date(2024, 7, 1), 2, ts + dt.timedelta(days=20), None, 1, "갱신", "24.08~26.08", "", 55000, None,
          2009]],
        column_names=["rent_id", "deal_date", "version", "valid_from", "valid_to", "is_current", "contract_type",
                      "contract_term", "use_rr_right", "pre_deposit_manwon", "pre_monthly_rent_manwon", "build_year"],
    )  # fmt: skip


# ───── 앱·클라이언트 (모든 API 테스트 공용) ─────


@pytest_asyncio.fixture(scope="session")
async def apps(stack):
    settings.cache_clear()
    from aptlake_api.main import create_internal_app, create_public_app

    public, internal = create_public_app(), create_internal_app()
    async with public.router.lifespan_context(public), internal.router.lifespan_context(internal):
        await public.state.res.redis.set("al:ds:ver", "gold@test.1")
        yield public, internal


@pytest_asyncio.fixture(scope="session")
async def pub(apps):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=apps[0], client=("10.9.0.1", 1)), base_url="http://t"
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def adm(apps):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1]), base_url="http://t") as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def admin_key(stack):
    import psycopg

    issued = keys.issue("test-pepper")
    with psycopg.connect(stack["su"], autocommit=True) as c:
        cid = c.execute("INSERT INTO api.client (name, plan_id) VALUES ('op','pro') RETURNING client_id").fetchone()[0]
        c.execute(
            "INSERT INTO api.api_key VALUES (%s,%s,%s,%s, now(), now() + interval '1 day', NULL, NULL)",
            (issued.key_id, cid, issued.secret_hmac, ["admin", "read"]),
        )
    return issued.api_key
