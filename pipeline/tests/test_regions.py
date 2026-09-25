import json
from pathlib import Path

from aptlake_pipeline.regions import derive_regions

FIX = Path(__file__).parent / "fixtures"


def test_derive_regions_from_real_rows():
    regions = {r.sgg_cd: r for r in derive_regions(json.loads((FIX / "stanregin_subset.json").read_text()))}
    # 읍면동 행(1111010100)과 시도 행은 제외
    assert set(regions) == {"41130", "41131", "41135", "36110", "11110", "12110"}
    # 일반구를 둔 시는 수집 대상이 아님 (실거래 API 에서 0건 확인)
    assert not regions["41130"].is_leaf
    assert regions["41135"].is_leaf and regions["41135"].sgg_nm == "성남시 분당구"
    assert regions["41135"].sido_nm == "경기도"
    # 세종: 시도 수준 행이 없으므로 시군구 행 자체가 시도
    assert regions["36110"].is_leaf and regions["36110"].sido_nm == "세종특별자치시"
    assert regions["11110"].full_nm == "서울특별시 종로구"
    assert regions["12110"].sido_cd == "12"
