"""거래 데이터 접근 (ClickHouse). WHERE 는 고정된 조각만 조립하고 값은 모두 서버측 바인딩."""

from __future__ import annotations

import datetime as dt
from typing import Any, cast

from clickhouse_connect.driver.asyncclient import AsyncClient
from psycopg_pool import AsyncConnectionPool

from .service import TradeFilter

Row = dict[str, Any]
TRADE_COLS = """trade_id, deal_date, complex_key, apt_nm, umd_nm, jibun, area_m2, floor, price_manwon, ppm2,
                is_cancelled, cancel_date, registered_date, apt_dong, deal_kind, seller_type, buyer_type,
                build_year, is_outlier, version, missing_since"""


def _where(f: TradeFilter) -> tuple[list[str], dict[str, Any]]:
    where = ["sgg_cd = {sgg:String}", "deal_date BETWEEN {a:Date} AND {b:Date}"]
    params: dict[str, Any] = {"sgg": f.sgg, "a": f.start, "b": f.end}
    if f.min_area is not None:
        where.append("area_m2 >= {min:Float64}")
        params["min"] = f.min_area
    if f.max_area is not None:
        where.append("area_m2 <= {max:Float64}")
        params["max"] = f.max_area
    if not f.include_cancelled:
        where.append("is_cancelled = 0")
    return where, params


class TradeRepository:
    def __init__(self, ch: AsyncClient):
        self.ch = ch

    async def _q(self, sql: str, params: dict[str, Any]) -> list[Row]:
        return list((await self.ch.query(sql, parameters=params)).named_results())

    async def page(self, f: TradeFilter, after: tuple[dt.date, str] | None, limit: int) -> list[Row]:
        where, params = _where(f)
        params["lim"] = limit
        if after:
            where.append("(deal_date, trade_id) < ({cd:Date}, {ck:String})")
            params["cd"], params["ck"] = after
        return await self._q(
            f"""SELECT {TRADE_COLS} FROM trade_current WHERE {" AND ".join(where)}
                ORDER BY deal_date DESC, trade_id DESC LIMIT {{lim:UInt32}}""",
            params,
        )

    async def summary(self, f: TradeFilter) -> Row:
        where, params = _where(f)
        rows = await self._q(
            f"""SELECT count() AS n, countIf(is_cancelled = 1) AS cancelled,
                       quantileExactInclusiveIf(0.5)(ppm2, is_cancelled = 0 AND is_outlier = 0 AND area_m2 > 0) AS med,
                       quantileExactInclusiveIf(0.5)(price_manwon, is_cancelled = 0) AS med_price
                FROM trade_current WHERE {" AND ".join(where)}""",
            params,
        )
        return rows[0]

    async def versions(self, trade_id: str, yyyymm: int) -> list[Row]:
        return await self._q(
            """
            SELECT version, valid_from, valid_to, is_current, is_cancelled, cancel_date, registered_date,
                   apt_dong, deal_kind, seller_type, buyer_type
            FROM trade_version
            WHERE trade_id = {id:String} AND toYYYYMM(deal_date) = {ym:UInt32}
            ORDER BY valid_from""",
            {"id": trade_id, "ym": yyyymm},
        )


CHUNK = 16  # 한 번에 읽는 발행 수 (발행당 변경 수백~수천 행 → 정렬이 작게 유지)


class ChangeRepository:
    """변경분 피드: 발행 기록(PostgreSQL ops.dataset_version) + 변경 행(ClickHouse trade_change)."""

    def __init__(self, pg: AsyncConnectionPool, ch: AsyncClient):
        self.pg, self.ch = pg, ch

    async def versions(self) -> list[Row]:
        async with self.pg.connection() as c:
            cur = await c.execute(
                """SELECT version, published_at, publish_id::text AS publish_id, snapshot_baseline
                   FROM ops.dataset_version"""
            )
            return cast(list[Row], await cur.fetchall())

    async def changes(self, publish_ids: list[str], after: tuple[int, str] | None, limit: int) -> list[Row]:
        """(r, trade_id) 키셋 페이지. r = 발행 순번(1부터, 버전 창 안의 위치).

        기본 키 (publish_id, trade_id) 를 타도록, 이어 읽는 발행 하나를 먼저 읽고 모자라면 다음 발행들을
        CHUNK 개씩 읽는다. 창 전체를 순번으로 정렬하면 90일 창(합성 36만 행)에서 페이지 하나가 약 2초였다.
        """
        out: list[Row] = []
        r0, k0 = after if after else (1, "")
        if after:  # 읽던 발행의 나머지
            out += await self._read([publish_ids[r0 - 1]], r0, "AND trade_id > {k0:String}", {"k0": k0}, limit)
            r0 += 1
        while len(out) < limit and r0 <= len(publish_ids):
            chunk = publish_ids[r0 - 1 : r0 - 1 + CHUNK]
            out += await self._read(chunk, r0, "", {}, limit - len(out))
            r0 += len(chunk)
        return out

    async def _read(self, ids: list[str], first: int, cond: str, extra: dict[str, Any], n: int) -> list[Row]:
        sql = f"""SELECT {first - 1} + indexOf({{ids:Array(String)}}, toString(publish_id)) AS r, change, changed_fields,
                         {TRADE_COLS}
                  FROM trade_change WHERE publish_id IN {{ids:Array(UUID)}} {cond}
                  ORDER BY r, trade_id LIMIT {{n:UInt32}}"""
        return list((await self.ch.query(sql, parameters={"ids": ids, "n": n, **extra})).named_results())
