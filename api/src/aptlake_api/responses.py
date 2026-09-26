"""orjson 으로 직렬화하는 JSON 응답 (FastAPI 의 ORJSONResponse 는 폐기 예정이라 같은 동작을 직접 둔다)."""

from __future__ import annotations

from typing import Any

import orjson
from starlette.responses import JSONResponse


class OrjsonResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)
