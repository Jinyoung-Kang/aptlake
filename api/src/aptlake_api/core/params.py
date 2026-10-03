"""라우터가 같이 쓰는 경로·쿼리 매개변수 형식 (HTTP 계층)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

YM_Q = r"^\d{4}-(0[1-9]|1[0-2])$"
SGG = Annotated[str, Path(pattern=r"^\d{5}$", description="시군구 코드 5자리 (법정동코드 앞 5자리)")]
