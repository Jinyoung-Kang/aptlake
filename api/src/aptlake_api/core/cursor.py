"""서명된 불투명 커서 (기획서 8-1). 위조·다른 질의에 재사용하면 400 INVALID_CURSOR."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import orjson


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def query_fingerprint(params: dict[str, Any]) -> str:
    return hashlib.sha256(orjson.dumps(params, option=orjson.OPT_SORT_KEYS)).hexdigest()[:16]


def encode(key: bytes, position: dict[str, Any], query_fp: str) -> str:
    payload = orjson.dumps({**position, "q": query_fp}, option=orjson.OPT_SORT_KEYS)
    sig = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(sig)}"


def decode(key: bytes, token: str, query_fp: str) -> dict[str, Any] | None:
    try:
        p, s = token.split(".", 1)
        payload, sig = _unb64(p), _unb64(s)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(key, payload, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = orjson.loads(payload)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.pop("q", None) != query_fp:
        return None
    return data
