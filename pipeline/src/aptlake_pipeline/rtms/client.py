"""국토교통부 아파트 매매 실거래가 API 클라이언트 (FR-101, FR-104).

- 한 (시군구, 계약월) = numOfRows 9999 로 보통 1 페이지 (W1 측정: 1,099건도 1회 응답).
- 호출 전 예산 차감 (Budget.reserve). 초당 호출 수는 토큰 버킷으로 제한.
- 재시도: 네트워크 오류·5xx·원천 일시 오류(01,02,04,05)는 지수 백오프 3회.
  한도 초과(22)는 실행 전체 중단, 키·요청 오류(10,11,12,20,30,31,32)는 즉시 실패.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .parse import Page, SourceError, parse_page

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
PAGE_SIZE = 9999
RETRYABLE_CODES = {"01", "02", "04", "05"}
QUOTA_CODES = {"22"}
MAX_ATTEMPTS = 3


class QuotaExceeded(Exception):
    pass


class FetchFailed(Exception):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class FetchResult:
    sgg_cd: str
    deal_ym: str
    pages: list[bytes]
    parsed: list[Page]
    fetched_at: float
    calls: int

    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        for p in self.pages:
            h.update(p)
        return h.hexdigest()

    @property
    def items(self) -> list[dict[str, str]]:
        return [i for p in self.parsed for i in p.items]


class TokenBucket:
    def __init__(self, rate: float):
        self.rate, self.tokens, self.last = rate, rate, time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class RtmsClient:
    def __init__(
        self,
        service_key: str,
        reserve: Callable[[int], None],
        tps: float = 10.0,
        http: httpx.AsyncClient | None = None,
        base_delay: float = 1.0,
    ):
        self._key = service_key
        self._reserve = reserve
        self._bucket = TokenBucket(tps)
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))
        self._base_delay = base_delay

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _page(self, sgg_cd: str, deal_ym: str, page_no: int) -> tuple[bytes, Page]:
        params: dict[str, str | int] = {
            "serviceKey": self._key,
            "LAWD_CD": sgg_cd,
            "DEAL_YMD": deal_ym,
            "pageNo": page_no,
            "numOfRows": PAGE_SIZE,
        }
        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._reserve(1)
            await self._bucket.take()
            try:
                resp = await self._http.get(URL, params=params)
                if resp.status_code >= 500:
                    raise FetchFailed(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    # 403 등은 키·승인 문제 → 재시도해도 소용없음. 응답 본문에 키가 섞이지 않도록 URL 은 남기지 않는다
                    raise FetchFailed(f"HTTP {resp.status_code}", code=str(resp.status_code))
                body = resp.content
                return body, parse_page(body.decode("utf-8"))
            except SourceError as e:
                if e.code in QUOTA_CODES:
                    raise QuotaExceeded(str(e)) from e
                if e.code not in RETRYABLE_CODES:
                    raise FetchFailed(str(e), code=e.code) from e
                last = e
            except FetchFailed as e:
                if e.code is not None:
                    raise
                last = e
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = FetchFailed(type(e).__name__)
            await asyncio.sleep(self._base_delay * (2**attempt) + random.random() * 0.2)  # noqa: S311
        raise FetchFailed(f"{sgg_cd}/{deal_ym} p{page_no}: {last}")

    async def fetch(self, sgg_cd: str, deal_ym: str) -> FetchResult:
        raw, first = await self._page(sgg_cd, deal_ym, 1)
        pages, parsed = [raw], [first]
        total_pages = max(1, -(-first.total_count // PAGE_SIZE))
        for p in range(2, total_pages + 1):
            raw, page = await self._page(sgg_cd, deal_ym, p)
            pages.append(raw)
            parsed.append(page)
        got = sum(len(p.items) for p in parsed)
        if got != first.total_count:
            raise FetchFailed(f"{sgg_cd}/{deal_ym}: totalCount {first.total_count} but received {got}")
        return FetchResult(sgg_cd, deal_ym, pages, parsed, time.time(), len(pages))
