"""API 키 형식과 검증 (기획서 8-1, 9장).

    al_live_<key_id>.<secret>
      key_id : 12자 (대문자·숫자, 혼동 문자 제외) — 조회용, 로그에 남겨도 됨
      secret : 32바이트 무작위 (base64url) — 서버는 HMAC-SHA256(pepper, secret) 만 저장
접두사 al_live_ 는 비밀 스캐너(gitleaks 등) 규칙으로 탐지할 수 있게 하기 위함이다.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

PREFIX = "al_live_"
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
KEY_RE = re.compile(r"^al_live_([2-9A-HJ-NP-Z]{12})\.([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True)
class IssuedKey:
    key_id: str
    api_key: str  # 발급 응답에서 1회만 노출
    secret_hmac: str  # 저장값


def hmac_secret(pepper: str, secret: str) -> str:
    return hmac.new(pepper.encode(), secret.encode(), hashlib.sha256).hexdigest()


def issue(pepper: str) -> IssuedKey:
    key_id = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    secret = secrets.token_urlsafe(32)
    return IssuedKey(key_id, f"{PREFIX}{key_id}.{secret}", hmac_secret(pepper, secret))


def parse(raw: str) -> tuple[str, str] | None:
    m = KEY_RE.fullmatch(raw.strip())
    return (m.group(1), m.group(2)) if m else None


def verify(pepper: str, secret: str, stored_hmac: str) -> bool:
    return hmac.compare_digest(hmac_secret(pepper, secret), stored_hmac)
