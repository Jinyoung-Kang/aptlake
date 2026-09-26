"""경계 단순화: 퇴화 링 제거, 작은 섬 조각 제거(가장 큰 조각 유지), 유효 도형, 면적 보존."""

from shapely.geometry import shape

from aptlake_pipeline.boundary import build_geometry, simplify


def square(x0: float, y0: float, size: float) -> list:
    return [[x0, y0], [x0 + size, y0], [x0 + size, y0 + size], [x0, y0 + size], [x0, y0]]


def test_degenerate_ring_is_dropped():
    g = {
        "type": "MultiPolygon",
        "coordinates": [
            [square(127.0, 37.0, 0.1)],
            [[[127.5, 37.5], [127.5, 37.5], [127.6, 37.6], [127.5, 37.5]]],  # 점 2개뿐인 퇴화 링
        ],
    }
    mp = build_geometry(g)
    assert mp.is_valid and len(mp.geoms) == 1


def test_simplify_keeps_largest_part_and_drops_tiny_islands():
    feats = [
        {
            "properties": {"sgg_cd": "11110"},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [square(127.0, 37.0, 0.1)],  # 본토 (약 100㎢)
                    [square(127.3, 37.3, 0.001)],  # 약 0.01㎢ 섬 → 제거
                    [square(127.4, 37.4, 0.02)],  # 약 4㎢ 섬 → 유지
                ],
            },
        },
        {"properties": {"sgg_cd": "11140"}, "geometry": {"type": "Polygon", "coordinates": [square(127.1, 37.0, 0.1)]}},
    ]
    geoms, err, quality = simplify(feats)
    a = shape(geoms["11110"])
    assert a.is_valid and len(a.geoms) == 2
    assert all(e < 0.01 for e in err.values())
    assert quality["overlap_pct"] < 0.01  # 맞닿은 두 정사각형은 겹치지 않는다
