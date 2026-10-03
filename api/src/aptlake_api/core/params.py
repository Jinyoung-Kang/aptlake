"""라우터가 같이 쓰는 경로·쿼리 매개변수 형식 (HTTP 계층)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

# 숫자는 [0-9] 로만 — \d 는 전각·아랍-인도 숫자 같은 유니코드 숫자도 받는다 (QA-003)
YM_Q = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
SGG = Annotated[str, Path(pattern=r"^[0-9]{5}$", description="시군구 코드 5자리 (법정동코드 앞 5자리)")]
