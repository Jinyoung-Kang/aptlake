from aptlake_pipeline.rone import map_cls_to_sido

# ops.region 에 들어가는 공식 시도 명칭 (법정동코드 API, 2026-09 기준)
SIDO = {
    "11": "서울특별시",
    "26": "부산광역시",
    "27": "대구광역시",
    "28": "인천광역시",
    "30": "대전광역시",
    "31": "울산광역시",
    "36": "세종특별자치시",
    "41": "경기도",
    "43": "충청북도",
    "44": "충청남도",
    "47": "경상북도",
    "48": "경상남도",
    "50": "제주특별자치도",
    "51": "강원특별자치도",
    "52": "전북특별자치도",
    "12": "전남광주통합특별시",
}


def test_rone_short_names_map_uniquely():
    expected = {
        "전국": "00",
        "서울": "11",
        "부산": "26",
        "대구": "27",
        "인천": "28",
        "대전": "30",
        "울산": "31",
        "세종": "36",
        "경기": "41",
        "강원": "51",
        "충북": "43",
        "충남": "44",
        "전북": "52",
        "경북": "47",
        "경남": "48",
        "제주": "50",
        "전남광주": "12",
    }
    for name, code in expected.items():
        assert map_cls_to_sido(name, SIDO) == code, name


def test_aggregates_are_not_mapped():
    for name in ["수도권", "지방", "5대광역시", "4대광역시", "7개도"]:
        assert map_cls_to_sido(name, SIDO) is None, name
