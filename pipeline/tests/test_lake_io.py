"""레이크 S3 요청 시간 제한이 카탈로그 속성 → PyIceberg FileIO → pyarrow S3 파일시스템까지 전달된다 (ADR-053).

기본값(3초)이면 VM 메모리 부족으로 MinIO 가 잠깐 멈출 때 HeadObject 가 curlCode 28 로 실패한다. PyIceberg 가 속성 이름을
바꾸거나 FileIO 기본 구현을 바꾸면 조용히 기본값으로 돌아가므로 실제 생성 경로로 확인한다.
"""

from __future__ import annotations

from pyiceberg.io import load_file_io
from pyiceberg.io.pyarrow import PyArrowFileIO

from aptlake_pipeline import lake


def test_lake_s3_requests_wait_longer_than_the_sdk_default():
    # RestCatalog 는 카탈로그 속성 위에 표 응답 설정(Lakekeeper 가 주는 엔드포인트·지역·단기 자격증명)을 얹어 FileIO 를 만든다
    table_config = {"s3.endpoint": "http://minio:9000", "s3.region": "us-east-1", "s3.path-style-access": "true"}
    io = load_file_io({**lake.CATALOG_PROPERTIES, **table_config}, "s3://lake/warehouse/t/metadata/v1.metadata.json")
    assert isinstance(io, PyArrowFileIO)
    kwargs = io.fs_by_scheme("s3", "lake").__reduce__()[1][0]
    assert kwargs["request_timeout"] == 30
