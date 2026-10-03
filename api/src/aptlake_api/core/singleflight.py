"""같은 키의 동시 계산을 하나로 합친다 (프로세스 안, QA-009 · 개선 제안 A2).

캐시가 빈 순간(발행 직후 데이터셋 버전이 바뀔 때 등) 같은 요청이 몰리면 첫 요청만 계산하고 나머지는 그 결과를 기다린다.
워커 프로세스마다 따로라 워커 수만큼은 겹칠 수 있다. 프로세스 사이까지 합치려면 Redis 잠금과 대기가 필요해 두지 않았다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable


class SingleFlight:
    def __init__(self) -> None:
        self._running: dict[Hashable, asyncio.Future] = {}

    async def do[T](self, key: Hashable, fn: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """(결과, 직접 계산했는가). 실패도 기다리던 요청 모두에 같은 예외로 전해진다.

        계산은 별도 태스크라, 처음 요청한 쪽의 연결이 끊겨(취소) 도 끝까지 돌아 기다리던 요청이 결과를 받는다.
        """
        task = self._running.get(key)
        leader = task is None
        if task is None:
            task = asyncio.ensure_future(fn())
            self._running[key] = task
            task.add_done_callback(lambda t: self._done(key, t))
        return await asyncio.shield(task), leader

    def _done(self, key: Hashable, task: asyncio.Future) -> None:
        if self._running.get(key) is task:
            del self._running[key]
        if not task.cancelled():
            task.exception()  # 기다리던 요청이 모두 끊긴 뒤 실패해도 '꺼내지 않은 예외' 경고를 남기지 않게

    def __len__(self) -> int:
        return len(self._running)
