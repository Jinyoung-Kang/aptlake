"""화면·응답에 내보내는 문자열의 비밀값 가림 (순수). 인증키 파라미터, URL 비밀번호, 인증 헤더, API 키, S3 자격증명."""

from __future__ import annotations

import re

_SENSITIVE_NAME = (
    r"(?:(?<![\w.-])key|(?<![\w.-])[\w.-]*?(?:service_?key|api[-_]?key|crtfc_key|access[-_]?key(?:[-_]?id)?"
    r"|secret(?:[-_]?access)?(?:[-_]?key)?|private[-_]?key|password|passwd|pwd|token|credentials?|signature))"
)
REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"al_live_[2-9A-HJ-NP-Z]{12}\.[A-Za-z0-9_-]{20,}"), "al_live_***"),
    # URL 사용자 정보의 비밀번호 (postgresql+psycopg://user:pw@, redis://:pw@ …)
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*)://([^:/@\s]*):([^@\s]+)@"), r"\1://\2:***@"),
    # 인증 헤더 (Bearer/Basic 스킴 뒤의 값까지)
    (
        re.compile(r"(?i)\b(authorization|x-api-key)([\"']?\s*[:=]\s*[\"']?)(?:(?:bearer|basic|token)\s+)?[^\s\"',}]+"),
        r"\1\2***",
    ),
    # name=value · "name": "value" · 'name': 'value'
    (re.compile(rf"(?i)({_SENSITIVE_NAME})([\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)[^\s\"',}}&]+"), r"\1\2***"),
]


def redact(text: str | None) -> str:
    if not text:
        return ""
    for pattern, repl in REDACTIONS:
        text = pattern.sub(repl, text)
    return text
