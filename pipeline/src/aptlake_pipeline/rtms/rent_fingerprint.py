"""전월세 계약 지문 키·동일 지문 순번·속성 해시 (ADR-050). 규칙은 매매(fingerprint.py)와 같다.

지문(rent_key) = 시군구 · 법정동(대응표 적용) · 정규화 지번 · 정규화 단지명 · 면적 · 계약일 · 보증금 · 월세 · 층.
나중에 바뀔 수 있는 값(계약 구분·계약 기간·갱신요구권·종전 보증금/월세·건축년도)은 속성 해시로 따로 비교한다.
전월세에는 동 항목이 없어 같은 지문의 계약이 매매보다 흔하다(탐침: 행의 5~17%) — 순번은 (지문, 속성 해시) 정렬로 정해
원천이 순서를 바꿔 줘도 같다. 단지 키는 매매와 같은 함수(같은 대응표)라 두 원천이 같은 단지 키로 이어진다.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from .fingerprint import UmdAliases, _h, complex_key, key_umd, norm_jibun, norm_text
from .rent_parse import TypedRent


def rent_key(t: TypedRent, aliases: UmdAliases | None = None) -> str:
    area = "" if t.area_m2 is None else f"{t.area_m2:.4f}"
    return _h(
        t.sgg_cd,
        key_umd(t.sgg_cd, t.umd_nm, aliases),
        norm_jibun(t.jibun),
        norm_text(t.apt_nm),
        area,
        t.deal_date.isoformat(),
        t.deposit_manwon,
        t.monthly_rent_manwon,
        t.floor,
    )


RENT_MUTABLE_FIELDS = (
    "contract_type",
    "contract_term",
    "use_rr_right",
    "pre_deposit_manwon",
    "pre_monthly_rent_manwon",
    "build_year",
)


def rent_attr_hash(t: TypedRent) -> str:
    return _h(*(getattr(t, f) for f in RENT_MUTABLE_FIELDS))[:32]


@dataclass(frozen=True)
class KeyedRent:
    rent: TypedRent
    rent_key: str
    dup_seq: int
    attr_hash: str
    complex_key: str

    @property
    def rent_id(self) -> str:
        """공개 식별자: 매매 거래 ID 와 같은 모양에 'r' 접두어 (시군구·계약월로 서빙 파티션 가지치기)."""
        ym = self.rent.deal_date.strftime("%Y%m")
        return f"r{self.rent.sgg_cd}-{ym}-{self.rent_key[:16]}-{self.dup_seq}"


def assign_rent_keys(rents: Iterable[TypedRent], aliases: UmdAliases | None = None) -> list[KeyedRent]:
    """한 (sgg_cd, deal_ym) 수집 결과 전체를 받아 지문·순번을 부여한다. 입력 순서와 무관한 결과."""
    groups: dict[str, list[tuple[str, TypedRent]]] = defaultdict(list)
    for t in rents:
        groups[rent_key(t, aliases)].append((rent_attr_hash(t), t))
    out: list[KeyedRent] = []
    for rk in sorted(groups):
        for seq, (ah, t) in enumerate(sorted(groups[rk], key=lambda x: x[0])):
            out.append(KeyedRent(t, rk, seq, ah, complex_key(t, aliases)))
    return out
