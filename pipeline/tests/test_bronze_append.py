"""bronze 적재의 일시 오류는 원천을 다시 부르지 않고 적재만 다시 한다 — 앞 시도가 커밋까지 됐으면 두 번 넣지 않는다."""

from __future__ import annotations

import pyarrow as pa
import pytest

from aptlake_pipeline import ingest
from aptlake_pipeline.rtms.client import FetchResult
from aptlake_pipeline.rtms.parse import parse_page

XML = (
    "<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header><body><items/>"
    "<numOfRows>9999</numOfRows><pageNo>1</pageNo><totalCount>0</totalCount></body></response>"
)


def _pending(sgg: str, body: str) -> tuple[FetchResult, str, list[dict]]:
    r = FetchResult(sgg, "202206", [body.encode()], [parse_page(XML)], 0.0, 1)
    return r, f"iid-{sgg}", [{"payload_sha256": r.sha256, "sgg_cd": sgg}]


class FakeTable:
    """append 가 처음 n 번은 OSError — commit_then_fail 이면 커밋은 된 뒤 연결만 끊긴 것처럼."""

    def __init__(self, fail: int, commit_then_fail: bool = False):
        self.fail, self.commit_then_fail, self.committed, self.appends = fail, commit_then_fail, [], 0

    def scan(self, **_):
        shas = [row["payload_sha256"] for row in self.committed]
        return type("S", (), {"to_arrow": lambda _s: pa.table({"payload_sha256": pa.array(shas, pa.string())})})()

    def append(self, arrow: pa.Table) -> None:
        self.appends += 1
        if self.fail:
            self.fail -= 1
            if self.commit_then_fail:
                self.committed += arrow.to_pylist()
            raise OSError("AWS Error NETWORK_CONNECTION during CreateMultipartUpload operation: curlCode: 28")
        self.committed += arrow.to_pylist()


@pytest.fixture
def rows(monkeypatch):
    # 시험용 행은 bronze 스키마의 두 열만 — 나머지는 스키마에 맞춰 None
    monkeypatch.setattr(ingest, "BRONZE_SCHEMA", pa.schema([("payload_sha256", pa.string()), ("sgg_cd", pa.string())]))
    return [_pending("11110", XML + " "), _pending("26350", XML + "  ")]


def test_transient_write_error_is_retried_without_calling_the_source_again(rows):
    t, waits = FakeTable(fail=2), []
    table, existing, n = ingest.append_bronze(lambda: t, "202206", rows, sleep=waits.append)
    assert table is t and existing == set() and n == 2 and t.appends == 3
    assert waits == [ingest.APPEND_BACKOFF_S, ingest.APPEND_BACKOFF_S * 2]
    assert len(t.committed) == 2


def test_commit_that_succeeded_before_the_error_is_not_appended_twice(rows):
    t = FakeTable(fail=1, commit_then_fail=True)
    _, existing, n = ingest.append_bronze(lambda: t, "202206", rows, sleep=lambda _: None)
    assert len(t.committed) == 2 and n == 0 and existing == {r.sha256 for r, _, _ in rows}


def test_gives_up_after_the_last_attempt(rows):
    t = FakeTable(fail=ingest.APPEND_ATTEMPTS)
    with pytest.raises(OSError):
        ingest.append_bronze(lambda: t, "202206", rows, sleep=lambda _: None)
    assert t.appends == ingest.APPEND_ATTEMPTS and not t.committed
