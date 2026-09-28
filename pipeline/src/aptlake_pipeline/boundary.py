"""시군구 경계 (지도용) — 국토정보플랫폼 V-World 2D 데이터 API `LT_C_ADSIGG_INFO`.

확인한 사실 (2026-09-26 실제 응답):
  - 전국 256개 피처, 속성 sig_cd·full_nm 이 행정안전부 법정동코드 기반 수집 대상(ops.region leaf)과
    코드·이름 모두 256/256 일치 (2026-07 통합 전남광주 12xxx, 인천 제물포구 28125 포함).
  - crs=EPSG:4326, 원본 약 52MB · 점 135만 개.

처리: 원본을 raw 버킷에 내용 주소로 보관 → 공식 목록과 대조(일치하는 코드만 저장, 불일치는 추측하지 않고 보고)
     → 시각화용 단순화: 퇴화 링(점 3개 미만) 제거 → 0.5㎢ 미만 섬 조각 제거(시군구마다 가장 큰 조각은 유지)
       → 시군구별 Douglas-Peucker(위상 보존, 0.001° ≈ 100m) → 1e-4° 격자 정밀도(유효 도형 보장).

단순화 방식 비교 (2026-09-26 측정, 전국 256개):
  topojson 공유 아크 단순화 : 최대 메모리 3.2GB → 파이프라인 컨테이너(2.5GB)에서 OOM
  GEOS coverage_simplify    : 0.5GB 이지만 V-World 이웃 경계가 꼭짓점을 정확히 공유하지 않아 점이 20만 개 넘게 남음
  시군구별 DP 0.001°        : 0.5GB·2초, 점 5.9만 개(1.1MB, gzip 0.3MB), 이웃 간 겹침 0.037%·틈 0.011%(면적 비율)
→ 틈·겹침이 화면에서 1px 미만이고 지도에 경계선을 그어 가려지므로 마지막 방식을 쓴다.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
from dataclasses import dataclass

import httpx
import shapely
from shapely.geometry import MultiPolygon, Polygon, mapping

from . import ops_db
from .config import settings
from .http_safe import checked
from .ingest import RAW_BUCKET, RawArchive

URL = "https://api.vworld.kr/req/data"
LAYER = "LT_C_ADSIGG_INFO"
SOURCE = f"V-World {LAYER}"
KOREA_BOX = "BOX(124,33,132,39)"
MIN_PART_KM2 = 0.5
DEG2_PER_KM2 = 1 / 9990.0  # 위도 약 36°에서 1°×1° ≈ 9,990㎢ (조각 크기 거르기용 근사)
DP_TOLERANCE = 0.001  # degrees
GRID = 1e-4


@dataclass
class BoundaryResult:
    matched: int
    unmatched_source: dict[str, str]
    missing_official: dict[str, str]
    name_mismatch: dict[str, tuple[str, str]]
    raw_bytes: int
    raw_sha256: str
    out_bytes: int
    area_err_median: float
    area_err_max: float
    overlap_pct: float = 0.0
    gap_pct: float = 0.0


def fetch_raw(client: httpx.Client | None = None) -> bytes:
    s = settings()
    with contextlib.ExitStack() as stack:  # 직접 만든 클라이언트만 닫는다
        http = client or stack.enter_context(httpx.Client(timeout=300))
        params: dict[str, str | int] = {
            "service": "data",
            "request": "GetFeature",
            "data": LAYER,
            "key": str(s.vworld_api_key),
            "domain": s.vworld_domain,
            "format": "json",
            "geomFilter": KOREA_BOX,
            "size": 1000,
            "page": 1,
            "geometry": "true",
            "attribute": "true",
            "crs": "EPSG:4326",
        }
        r = http.get(URL, params=params)
        checked(r, "V-World")
        body = r.content
        resp = json.loads(body)["response"]
        if resp.get("status") != "OK":
            # 오류 본문에 키가 섞일 수 있으므로 상태·오류 코드만 남긴다
            raise RuntimeError(f"V-World status={resp.get('status')} error={resp.get('error', {}).get('code')}")
        total, pages = int(resp["record"]["total"]), int(resp["page"]["total"])
        if pages != 1 or total > 1000:
            raise RuntimeError(f"unexpected paging: total={total} pages={pages} (한 페이지 1,000건 가정이 깨짐)")
        return body


def _parts(g) -> list[Polygon]:
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for x in getattr(g, "geoms", []) for p in _parts(x)]


def _clean_ring(coords: list) -> list[tuple[float, float]] | None:
    out: list[tuple[float, float]] = []
    for c in coords:
        pt = (float(c[0]), float(c[1]))
        if not out or out[-1] != pt:
            out.append(pt)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out if len(set(out)) >= 3 else None  # 원본에 면적 없는 퇴화 링이 섞여 있다 (실측 1개)


def build_geometry(geojson_geometry: dict) -> MultiPolygon:
    polys = (
        [geojson_geometry["coordinates"]] if geojson_geometry["type"] == "Polygon" else geojson_geometry["coordinates"]
    )
    out = []
    for poly in polys:
        ext = _clean_ring(poly[0])
        if ext is None:
            continue
        out.append(Polygon(ext, [h for h in (_clean_ring(x) for x in poly[1:]) if h]))
    return MultiPolygon(_parts(shapely.make_valid(MultiPolygon(out))))


def simplify(features: list[dict]) -> tuple[dict[str, dict], dict[str, float], dict[str, float]]:
    """{sgg_cd: GeoJSON geometry}, {sgg_cd: 면적 상대 오차}, 전체 품질 지표(겹침·틈 %)."""
    min_area = MIN_PART_KM2 * DEG2_PER_KM2
    originals: dict[str, MultiPolygon] = {}
    for f in features:
        parts = sorted(_parts(build_geometry(f["geometry"])), key=lambda p: p.area, reverse=True)
        originals[f["properties"]["sgg_cd"]] = MultiPolygon([parts[0]] + [p for p in parts[1:] if p.area >= min_area])
    simplified: dict[str, MultiPolygon] = {}
    for code, g in originals.items():
        s = shapely.set_precision(shapely.make_valid(shapely.simplify(g, DP_TOLERANCE, preserve_topology=True)), GRID)
        s = MultiPolygon(_parts(s))
        if s.is_empty or not s.is_valid:
            raise RuntimeError(f"simplified geometry invalid for {code}")
        simplified[code] = s
    err = {c: abs(simplified[c].area - originals[c].area) / originals[c].area for c in originals}
    orig_union = shapely.union_all(list(originals.values())).area
    union = shapely.union_all(list(simplified.values())).area
    total = sum(g.area for g in simplified.values())
    quality = {
        "overlap_pct": round((total - union) / union * 100, 4),
        "gap_pct": round((orig_union - union) / orig_union * 100, 4),
    }
    return {c: mapping(g) for c, g in simplified.items()}, err, quality


def refresh() -> BoundaryResult:
    raw = fetch_raw()
    sha = hashlib.sha256(raw).hexdigest()
    archive = RawArchive()
    key = f"vworld/{LAYER}/{sha}.json"
    try:
        archive.s3.head_object(Bucket=RAW_BUCKET, Key=key)
    except Exception:  # noqa: BLE001 — 없으면 올린다 (내용 주소 키)
        archive.s3.put_object(Bucket=RAW_BUCKET, Key=key, Body=raw, ContentType="application/json")

    feats = json.loads(raw)["response"]["result"]["featureCollection"]["features"]
    source = {f["properties"]["sig_cd"]: f for f in feats}
    with ops_db.conn() as c:
        official = {
            r["sgg_cd"]: r["full_nm"]
            for r in c.execute("SELECT sgg_cd, full_nm FROM ops.region WHERE is_leaf AND active").fetchall()
        }
    matched = [code for code in source if code in official and source[code]["properties"]["full_nm"] == official[code]]
    name_mismatch = {
        code: (official[code], source[code]["properties"]["full_nm"])
        for code in source
        if code in official and code not in matched
    }
    geoms, err, quality = simplify(
        [{"properties": {"sgg_cd": code}, "geometry": source[code]["geometry"]} for code in matched]
    )

    now = dt.datetime.now(tz=dt.UTC)
    params: dict[str, object] = {
        "min_part_km2": MIN_PART_KM2,
        "dp_tolerance_deg": DP_TOLERANCE,
        "grid_deg": GRID,
        **quality,
        "raw_uri": f"s3://{RAW_BUCKET}/{key}",
    }
    with ops_db.conn() as c, c.transaction():
        c.execute("DELETE FROM ops.region_boundary")
        c.cursor().executemany(
            """INSERT INTO ops.region_boundary
                   (sgg_cd, geometry, area_rel_error, source, source_sha256, simplify, fetched_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            [(code, json.dumps(geoms[code]), err[code], SOURCE, sha, json.dumps(params), now) for code in matched],
        )
    errs = sorted(err.values())
    return BoundaryResult(
        matched=len(matched),
        unmatched_source={c: source[c]["properties"]["full_nm"] for c in source if c not in official},
        missing_official={c: n for c, n in official.items() if c not in source},
        name_mismatch=name_mismatch,
        raw_bytes=len(raw),
        raw_sha256=sha,
        out_bytes=sum(len(json.dumps(g)) for g in geoms.values()),
        area_err_median=errs[len(errs) // 2] if errs else 0.0,
        area_err_max=errs[-1] if errs else 0.0,
        overlap_pct=quality["overlap_pct"],
        gap_pct=quality["gap_pct"],
    )
