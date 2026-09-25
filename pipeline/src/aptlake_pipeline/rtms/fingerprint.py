"""거래 지문 키·동일 지문 순번·속성 해시 (기획서 6-1, FR-202).

원천에는 거래 고유 ID 가 없다. 사후에 바뀌지 않는다고 보는 필드만 지문(trade_key)에 넣고,
나중에 바뀌는 필드(해제여부·해제일·등기일·동명·거래유형 등)는 attr_hash 로 따로 비교한다.

동일 지문 거래가 N건이면 dup_seq 0..N-1 로 구별한다. 순번은 입력 순서가 아니라
(trade_key, attr_hash) 정렬로 정하므로, API 가 같은 결과를 다른 순서로 돌려줘도 같은 순번이 나온다.
한계: 동일 지문 그룹 안에서 한 건의 속성만 바뀌면 정렬 위치가 바뀔 수 있어 두 건이 동시에 '변경'으로
보일 수 있다. 이 그룹 수를 품질 지표(dup_fingerprint_groups)로 공개한다.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .parse import TypedTrade

_WS = re.compile(r"\s+")


def norm_text(s: str | None) -> str:
    """표기 차이 흡수용 정규화: NFKC(전각→반각) + 공백 제거 + 영문 소문자."""
    if not s:
        return ""
    return _WS.sub("", unicodedata.normalize("NFKC", s)).lower()


def norm_jibun(s: str | None) -> str:
    """'0202-0003' → '202-3', ' 산 12 ' → '산12'. 숫자 부분의 앞자리 0 만 제거한다."""
    t = norm_text(s)
    return re.sub(r"\d+", lambda m: str(int(m.group(0))), t)


def display_text(s: str | None) -> str:
    """화면 표시용: NFKC + 연속 공백 1칸."""
    if not s:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", s)).strip()


def _h(*parts: object) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def complex_key(t: TypedTrade) -> str:
    return "c_" + _h(t.sgg_cd, norm_text(t.umd_nm), norm_jibun(t.jibun), norm_text(t.apt_nm))[:20]


def trade_key(t: TypedTrade) -> str:
    area = "" if t.area_m2 is None else f"{t.area_m2:.4f}"
    return _h(
        t.sgg_cd,
        norm_text(t.umd_nm),
        norm_jibun(t.jibun),
        norm_text(t.apt_nm),
        area,
        t.deal_date.isoformat(),
        t.price_manwon,
        t.floor,
    )


MUTABLE_FIELDS = (
    "is_cancelled",
    "cancel_date",
    "registered_date",
    "apt_dong",
    "deal_kind",
    "agent_sgg_nm",
    "seller_type",
    "buyer_type",
    "build_year",
    "land_leasehold",
)


def attr_hash(t: TypedTrade) -> str:
    return _h(*(getattr(t, f) for f in MUTABLE_FIELDS))[:32]


@dataclass(frozen=True)
class KeyedTrade:
    trade: TypedTrade
    trade_key: str
    dup_seq: int
    attr_hash: str
    complex_key: str

    @property
    def trade_id(self) -> str:
        """공개 식별자: 시군구·계약월을 앞에 둬 서빙 계층에서 파티션 가지치기가 된다."""
        ym = self.trade.deal_date.strftime("%Y%m")
        return f"{self.trade.sgg_cd}-{ym}-{self.trade_key[:16]}-{self.dup_seq}"


def assign_keys(trades: Iterable[TypedTrade]) -> list[KeyedTrade]:
    """한 (sgg_cd, deal_ym) 수집 결과 전체를 받아 지문·순번을 부여한다. 입력 순서와 무관한 결과."""
    groups: dict[str, list[tuple[str, TypedTrade]]] = defaultdict(list)
    for t in trades:
        groups[trade_key(t)].append((attr_hash(t), t))
    out: list[KeyedTrade] = []
    for tk in sorted(groups):
        for seq, (ah, t) in enumerate(sorted(groups[tk], key=lambda x: x[0])):
            out.append(KeyedTrade(t, tk, seq, ah, complex_key(t)))
    return out


def duplicate_groups(keyed: Iterable[KeyedTrade]) -> int:
    """동일 지문 그룹 수 (dup_seq > 0 이 존재하는 trade_key 수)."""
    return len({k.trade_key for k in keyed if k.dup_seq > 0})
