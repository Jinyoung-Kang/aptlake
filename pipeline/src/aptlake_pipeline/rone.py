"""R-ONE 기준 지수 (자체 지수 검증용, FR-402).

사용 통계표 (SttsApiTbl 목록에서 확인): A_2024_00178 「(월) 지역별 매매지수_아파트」, 2006-01~, 기준 2026.06=100.
지역 분류(CLS)는 '서울', '경기', '충북', '전남광주' 같은 약칭이다. 약칭→시도코드 표를 손으로 쓰지 않고,
공식 시도 명칭(ops.region)에 대해 '접두어 일치, 없으면 글자 순서 포함(subsequence)'이 정확히 하나일 때만
매핑한다. '수도권'·'지방' 같은 묶음이나 모호한 이름은 매핑하지 않는다.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pyarrow as pa

from . import ops_db
from .config import settings
from .lake import catalog

URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do"
ITEMS_URL = "https://www.reb.or.kr/r-one/openapi/SttsApiTblItm.do"
STATBL_ID = "A_2024_00178"
SOURCE = f"R-ONE {STATBL_ID} (월) 지역별 매매지수_아파트"
NATIONWIDE = "전국"


def _is_subsequence(short: str, full: str) -> bool:
    it = iter(full)
    return all(ch in it for ch in short)


def map_cls_to_sido(cls_name: str, sido_names: dict[str, str]) -> str | None:
    if cls_name == NATIONWIDE:
        return "00"
    prefix = [c for c, n in sido_names.items() if n.startswith(cls_name)]
    if len(prefix) == 1:
        return prefix[0]
    if prefix:
        return None
    subseq = [c for c, n in sido_names.items() if _is_subsequence(cls_name, n)]
    return subseq[0] if len(subseq) == 1 else None


def _get(http: httpx.Client, url: str, **params: str | int) -> list[dict]:
    key = str(settings().reb_api_key)
    rows: list[dict] = []
    page = 1
    while True:
        r = http.get(url, params={"KEY": key, "Type": "json", "pIndex": page, "pSize": 1000, **params})
        r.raise_for_status()
        body = r.json()
        name = next(iter(body))
        if name == "RESULT":  # 오류 응답 형식
            raise RuntimeError(f"R-ONE {body['RESULT']}")
        head = body[name][0]["head"]
        code = head[1]["RESULT"]["CODE"]
        if code != "INFO-000":
            raise RuntimeError(f"R-ONE {code}")
        part = body[name][1]["row"]
        rows.extend(part)
        if len(rows) >= int(head[0]["list_total_count"]) or not part:
            return rows
        page += 1


def refresh() -> dict[str, object]:
    with ops_db.conn() as c:
        sido = {
            r["sido_cd"]: r["sido_nm"]
            for r in c.execute("SELECT DISTINCT sido_cd, sido_nm FROM ops.region WHERE active").fetchall()
        }
    http = httpx.Client(timeout=60)
    items = _get(http, ITEMS_URL, STATBL_ID=STATBL_ID)
    top = [i for i in items if i["ITM_TAG"] == "분류" and "&gt;" not in i["ITM_FULLNM"] and ">" not in i["ITM_FULLNM"]]
    mapping: dict[int, str] = {}
    for i in top:
        region = map_cls_to_sido(i["ITM_NM"], sido)
        if region:
            mapping[int(i["ITM_ID"])] = region
    now = dt.datetime.now(tz=dt.UTC)
    out = []
    for cls_id, region in mapping.items():
        for r in _get(http, URL, STATBL_ID=STATBL_ID, DTACYCLE_CD="MM", CLS_ID=cls_id):
            if r.get("DTA_VAL") is None:
                continue
            out.append(
                {
                    "region_id": region,
                    "period": r["WRTTIME_IDTFR_ID"],
                    "value": float(r["DTA_VAL"]),
                    "source": SOURCE,
                    "fetched_at": now,
                }
            )
    table = catalog().load_table("gold.index_reference")
    table.overwrite(
        pa.Table.from_pylist(
            out,
            schema=pa.schema(
                [
                    ("region_id", pa.string()),
                    ("period", pa.string()),
                    ("value", pa.float64()),
                    ("source", pa.string()),
                    ("fetched_at", pa.timestamp("us", tz="UTC")),
                ]
            ),
        )
    )
    return {
        "regions": sorted(mapping.values()),
        "rows": len(out),
        "unmapped": sorted(i["ITM_NM"] for i in top if int(i["ITM_ID"]) not in mapping),
    }
