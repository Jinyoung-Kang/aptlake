"""시군구 목록 (행정안전부 법정동코드 API, StanReginCd) → ops.region + silver.region.

확인한 사실 (tools 스파이크, 2026-09):
  - flag=Y(현존) 20,560행, 그중 시군구 수준(읍면동·리 코드 000/00, 시군구 코드 ≠ 000) 269개.
  - 일반구를 둔 시(예: 41130 성남시)는 실거래 API 에서 0건, 거래는 구 코드(41131·41133·41135)로 조회된다.
    locathigh_cd 는 구의 상위를 시가 아니라 도로 가리키므로, 상위 시 판별은 공식 명칭 포함 관계
    ('경기도 성남시' 가 '경기도 성남시 분당구' 의 접두어)로 한다 → 수집 대상(leaf) 256개.
  - 2026-07 통합(전남광주통합특별시, 시도 12)에 맞춰 실거래 API 도 과거 계약월을 새 코드로 제공한다
    (12110 은 2024-09 거래를 반환, 옛 29110 은 0건). 따라서 현존 코드만으로 전 기간을 수집한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx
import pyarrow as pa

from . import ops_db
from .config import settings
from .lake import catalog

URL = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
SOURCE = "MOIS StanReginCd"


@dataclass(frozen=True)
class Region:
    sgg_cd: str
    sido_cd: str
    sido_nm: str
    sgg_nm: str
    full_nm: str
    is_leaf: bool


def fetch_rows(client: httpx.Client | None = None) -> list[dict]:
    key = str(settings().data_go_kr_key)
    http = client or httpx.Client(timeout=60)
    rows: list[dict] = []
    page = 1
    while True:
        r = http.get(URL, params={"serviceKey": key, "type": "json", "pageNo": page, "numOfRows": 1000, "flag": "Y"})
        r.raise_for_status()
        body = r.json()["StanReginCd"]
        head = body[0]["head"]
        result = head[2]["RESULT"]["resultCode"]
        if result != "INFO-0":
            raise RuntimeError(f"StanReginCd {result}")
        part = body[1]["row"] if len(body) > 1 else []
        rows.extend(part)
        if not part or len(rows) >= int(head[0]["totalCount"]):
            return rows
        page += 1


def derive_regions(rows: list[dict]) -> list[Region]:
    sido_names = {
        r["sido_cd"]: r["locatadd_nm"].strip()
        for r in rows
        if r["sgg_cd"] == "000" and r["umd_cd"] == "000" and r["ri_cd"] == "00"
    }
    sgg = [r for r in rows if r["umd_cd"] == "000" and r["ri_cd"] == "00" and r["sgg_cd"] != "000"]
    names = {r["region_cd"][:5]: r["locatadd_nm"].strip() for r in sgg}
    parents = {c for c, n in names.items() if any(o != c and on.startswith(n + " ") for o, on in names.items())}
    out = []
    for r in sgg:
        code, full = r["region_cd"][:5], names[r["region_cd"][:5]]
        sido_nm = sido_names.get(r["sido_cd"], full)  # 세종: 시도 수준 행이 없고 시군구 행 = 시도
        sgg_nm = full[len(sido_nm) :].strip() if full.startswith(sido_nm + " ") else full
        out.append(Region(code, r["sido_cd"], sido_nm, sgg_nm, full, code not in parents))
    return sorted(out, key=lambda x: x.sgg_cd)


def refresh() -> dict[str, int]:
    regions = derive_regions(fetch_rows())
    if len(regions) < 200:  # 원천 이상(부분 응답) 방어: 기존 목록을 덮어쓰지 않는다
        raise RuntimeError(f"too few regions from source: {len(regions)}")
    now = dt.datetime.now(tz=dt.UTC)
    with ops_db.conn() as c:
        c.execute("UPDATE ops.region SET active = false")
        c.cursor().executemany(
            """INSERT INTO ops.region (sgg_cd, sido_cd, sido_nm, sgg_nm, full_nm, is_leaf, active, source, fetched_at)
               VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)
               ON CONFLICT (sgg_cd) DO UPDATE SET sido_cd=EXCLUDED.sido_cd, sido_nm=EXCLUDED.sido_nm,
                 sgg_nm=EXCLUDED.sgg_nm, full_nm=EXCLUDED.full_nm, is_leaf=EXCLUDED.is_leaf, active=true,
                 source=EXCLUDED.source, fetched_at=EXCLUDED.fetched_at""",
            [(r.sgg_cd, r.sido_cd, r.sido_nm, r.sgg_nm, r.full_nm, r.is_leaf, SOURCE, now) for r in regions],
        )
    leaf = [r for r in regions if r.is_leaf]
    table = catalog().load_table("silver.region")
    table.overwrite(
        pa.Table.from_pylist(
            [
                {
                    "sgg_cd": r.sgg_cd,
                    "sido_cd": r.sido_cd,
                    "sido_nm": r.sido_nm,
                    "sgg_nm": r.sgg_nm,
                    "full_nm": r.full_nm,
                    "source": SOURCE,
                    "fetched_at": now,
                }
                for r in leaf
            ],
            schema=pa.schema(
                [
                    ("sgg_cd", pa.string()),
                    ("sido_cd", pa.string()),
                    ("sido_nm", pa.string()),
                    ("sgg_nm", pa.string()),
                    ("full_nm", pa.string()),
                    ("source", pa.string()),
                    ("fetched_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        )
    )
    return {"sgg_level": len(regions), "leaf": len(leaf), "parents": len(regions) - len(leaf)}
