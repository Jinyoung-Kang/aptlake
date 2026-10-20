"""0단계 탐침: 아파트 전월세 실거래 응답의 항목·값 형식을 실제 응답으로 확인한다 (키는 출력하지 않음).

  uv run --project pipeline python tools/probe_rent.py 11110:202407 26350:202407 41830:202407
  DUMP=<디렉터리> 를 주면 받은 원본 XML 을 rent_<시군구>_<월>_p<쪽>.xml 로 남긴다.
  TRADE=1 이면 같은 파티션의 매매(상세판) 건수도 받아 비율을 낸다 (매매 예산 1회씩).

확인하는 것 (설계 계획서 §1-3): 항목 집합 · 빈 값 형식 · deposit/monthlyRent 문자열 형식 ·
contractType · useRRRight · contractTerm 의 실제 값 목록 · 월세 0 인 행 비율 · 해제 항목 유무 · aptSeq 유무 · 건수.
"""

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probe_tradedev import DEV, blank_kind, fetch_all, load_env  # noqa: E402

RENT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
VALUE_LISTS = ("contractType", "useRRRight", "contractTerm", "landCd", "roadNmbCd")


def shape(v: str | None) -> str:
    """값의 모양: 숫자 → 9, 그 밖 문자는 그대로 (예 '1,234' → '9,999')."""
    if v is None:
        return "<없음>"
    return re.sub(r"\d", "9", v) if v.strip() else repr(v)


def probe(lawd: str, ym: str, dump: Path | None, with_trade: bool) -> dict:
    items, bodies, meta = fetch_all(RENT, lawd, ym, "전월세")
    if dump:
        dump.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(bodies):
            (dump / f"rent_{lawd}_{ym}_p{i + 1}.xml").write_text(b)
    print(f"\n## {lawd} {ym}")
    print(
        f"- totalCount {meta['total']} · 받은 행 {meta['items']} · numOfRows 응답 {meta['numOfRows']} · 쪽 {meta['pages']} · {meta['ms']:.0f}ms"
    )
    fields = Counter(f for i in items for f in i)
    print(f"- 항목({len(fields)}): {sorted(fields)}")
    partial = {f: c for f, c in fields.items() if c != len(items)}
    print(f"- 행마다 항목이 다 오는지: {partial or '예'}")
    print("- 항목별 빈 값 형식 (값 / 공백 / 빈문자 / 없음) · 값 모양 상위:")
    for f in sorted(fields):
        kinds = Counter(blank_kind(r.get(f)) for r in items)
        shapes = Counter(shape(r.get(f)) for r in items if blank_kind(r.get(f)) == "값").most_common(4)
        print(f"  {f}: {dict(kinds)} 모양 {shapes}")
    for f in VALUE_LISTS:
        if f in fields:
            print(f"- {f} 값 목록: {Counter(r.get(f) for r in items).most_common(12)}")
    if "monthlyRent" in fields:
        zero = sum(1 for r in items if (r.get("monthlyRent") or "").strip().replace(",", "") == "0")
        blank = sum(1 for r in items if not (r.get("monthlyRent") or "").strip())
        print(f"- monthlyRent '0' {zero}행 ({zero / max(1, len(items)):.1%}) · 빈 값 {blank}행")
    cancel = [f for f in fields if "cdeal" in f.lower() or "cancel" in f.lower()]
    print(f"- 해제 관련 항목: {cancel or '없음'}")
    # 계약일이 요청 월과 같은지 (DEAL_YMD 가 계약월 기준인지)
    yms = Counter(f"{(r.get('dealYear') or '').strip()}{(r.get('dealMonth') or '').strip().zfill(2)}" for r in items)
    print(f"- 계약 연월 분포: {dict(yms)}")
    out = {"rent": meta["total"]}
    if with_trade:
        trade_items, _, tm = fetch_all(DEV, lawd, ym, "매매")
        out["trade"] = tm["total"]
        print(f"- 같은 파티션 매매 {tm['total']}건 → 전월세/매매 {meta['total'] / max(1, tm['total']):.2f}배")
    return out


if __name__ == "__main__":
    load_env()
    dump = Path(os.environ["DUMP"]) if os.environ.get("DUMP") else None
    res = [probe(*a.split(":"), dump, bool(os.environ.get("TRADE"))) for a in sys.argv[1:]]
    if all("trade" in r for r in res):
        rent, trade = sum(r["rent"] for r in res), sum(r["trade"] for r in res)
        print(f"\n## 합계: 전월세 {rent} · 매매 {trade} → {rent / max(1, trade):.2f}배")
