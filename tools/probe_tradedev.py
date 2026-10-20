"""0단계 탐침: 매매 기본판과 상세판(TradeDev)을 같은 (시군구, 계약월)로 받아 비교한다 (키는 출력하지 않음).

  uv run --project pipeline python tools/probe_tradedev.py 11110:202407 26350:202407 41830:202407
  DUMP=<디렉터리> 를 주면 받은 원본 XML 을 base_/dev_<시군구>_<월>.xml 로 남긴다.

확인하는 것 (설계 계획서 docs/plans/2026-10-tradedev-and-rent-design.md §1):
  - 공통 20개 항목이 바이트 단위로 같은지 (빈 값 공백 한 칸·YY.MM.DD·쉼표 금액 포함)
  - (trade_key, dup_seq, attr_hash) 집합이 같은지 — 지문 모듈을 그대로 적용
  - 새 항목의 채움률, aptSeq ↔ complex_key 가 1:1 인지
  - numOfRows=9999 · totalCount 동작
"""

import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline" / "src"))

from aptlake_pipeline.rtms.fingerprint import assign_keys, complex_key  # noqa: E402
from aptlake_pipeline.rtms.parse import SOURCE_FIELDS_V1, parse_page, portal_error_code, to_typed  # noqa: E402

BASE = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
DEV = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
ROWS = 9999


def load_env(path: Path = ROOT / ".env") -> None:
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def call(url: str, lawd: str, ym: str, page: int = 1) -> tuple[int, str, float]:
    """(HTTP 상태, 본문, ms). 오류여도 본문을 돌려주고, 예외 문자열에 키가 든 URL 이 실리지 않게 한다."""
    q = urllib.parse.urlencode(
        {"serviceKey": os.environ["DATA_GO_KR_KEY"], "LAWD_CD": lawd, "DEAL_YMD": ym, "pageNo": page, "numOfRows": ROWS}
    )
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=60) as r:  # noqa: S310 — 고정된 https 주소
            status, body = r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    return status, body, (time.perf_counter() - t) * 1000


def fetch_all(url: str, lawd: str, ym: str, label: str) -> tuple[list[dict[str, str]], list[str], dict]:
    status, body, ms = call(url, lawd, ym)
    if status != 200:
        raise SystemExit(f"{label} {lawd}/{ym}: HTTP {status} reason {portal_error_code(body)}")
    first = parse_page(body)
    pages, bodies = [first], [body]
    for p in range(2, max(1, -(-first.total_count // ROWS)) + 1):
        status, body, _ = call(url, lawd, ym, p)
        if status != 200:
            raise SystemExit(f"{label} {lawd}/{ym} p{p}: HTTP {status} reason {portal_error_code(body)}")
        pages.append(parse_page(body))
        bodies.append(body)
    items = [i for pg in pages for i in pg.items]
    meta = {
        "total": first.total_count,
        "numOfRows": first.num_of_rows,
        "pages": len(pages),
        "items": len(items),
        "ms": ms,
    }
    return items, bodies, meta


def blank_kind(v: str | None) -> str:
    if v is None:
        return "없음"
    if v == "":
        return "빈문자"
    if v.strip() == "":
        return f"공백{len(v)}"
    return "값"


def compare(lawd: str, ym: str, dump: Path | None) -> dict:
    base, base_bodies, bm = fetch_all(BASE, lawd, ym, "기본판")
    dev, dev_bodies, dm = fetch_all(DEV, lawd, ym, "상세판")
    if dump:
        dump.mkdir(parents=True, exist_ok=True)
        for i, b in enumerate(base_bodies):
            (dump / f"base_{lawd}_{ym}_p{i + 1}.xml").write_text(b)
        for i, b in enumerate(dev_bodies):
            (dump / f"dev_{lawd}_{ym}_p{i + 1}.xml").write_text(b)

    print(f"\n## {lawd} {ym}")
    print(
        f"- 기본판: totalCount {bm['total']} · 받은 행 {bm['items']} · numOfRows 응답 {bm['numOfRows']} · 쪽 {bm['pages']} · {bm['ms']:.0f}ms"
    )
    print(
        f"- 상세판: totalCount {dm['total']} · 받은 행 {dm['items']} · numOfRows 응답 {dm['numOfRows']} · 쪽 {dm['pages']} · {dm['ms']:.0f}ms"
    )

    base_fields = Counter(f for i in base for f in i)
    dev_fields = Counter(f for i in dev for f in i)
    extra = sorted(set(dev_fields) - set(SOURCE_FIELDS_V1))
    missing = sorted(set(SOURCE_FIELDS_V1) - set(dev_fields))
    print(f"- 기본판 항목 집합 = V1: {set(base_fields) == set(SOURCE_FIELDS_V1)}")
    print(f"- 상세판에만 있는 항목({len(extra)}): {extra}")
    print(f"- 상세판에 없는 V1 항목: {missing}")
    partial = {f: c for f, c in dev_fields.items() if c != len(dev)}
    print(f"- 상세판 행마다 항목이 다 오는지: {partial or '예'}")

    # 공통 20개 항목: 바이트 단위 (순서 무관 다중집합)
    def common(rows: list[dict[str, str]]) -> Counter:
        return Counter(tuple(r.get(f) for f in SOURCE_FIELDS_V1) for r in rows)

    cb, cd = common(base), common(dev)
    same_common = cb == cd
    print(f"- 공통 20개 항목 바이트 동일(다중집합): {same_common}")
    if not same_common:
        only_b, only_d = cb - cd, cd - cb
        print(f"  기본판에만 {sum(only_b.values())}행, 상세판에만 {sum(only_d.values())}행")
        for f in SOURCE_FIELDS_V1:
            vb = Counter(r.get(f) for r in base)
            vd = Counter(r.get(f) for r in dev)
            if vb != vd:
                diff_b = list((vb - vd).items())[:3]
                diff_d = list((vd - vb).items())[:3]
                print(f"  항목 {f}: 기본판에만 {diff_b} / 상세판에만 {diff_d}")

    # 지문 집합
    kb = {(k.trade_key, k.dup_seq, k.attr_hash) for k in assign_keys(to_typed(r) for r in base)}
    kd = {(k.trade_key, k.dup_seq, k.attr_hash) for k in assign_keys(to_typed(r) for r in dev)}
    print(f"- (trade_key, dup_seq, attr_hash) 집합 동일: {kb == kd} (기본판 {len(kb)} · 상세판 {len(kd)})")

    # 새 항목 빈 값 형식·채움률
    print("- 새 항목 채움 (값 / 공백 / 빈문자 / 없음):")
    for f in extra:
        kinds = Counter(blank_kind(r.get(f)) for r in dev)
        sample = next((r[f] for r in dev if blank_kind(r.get(f)) == "값"), None)
        print(f"  {f}: {dict(kinds)} 예 {sample!r}")

    # aptSeq ↔ complex_key
    if "aptSeq" in dev_fields:
        ck_to_seq: dict[str, set[str]] = defaultdict(set)
        seq_to_ck: dict[str, set[str]] = defaultdict(set)
        for r in dev:
            seq = (r.get("aptSeq") or "").strip()
            if not seq:
                continue
            ck = complex_key(to_typed(r))
            ck_to_seq[ck].add(seq)
            seq_to_ck[seq].add(ck)
        multi_seq = {k: v for k, v in ck_to_seq.items() if len(v) > 1}
        multi_ck = {k: v for k, v in seq_to_ck.items() if len(v) > 1}
        print(f"- complex_key {len(ck_to_seq)}개 · aptSeq {len(seq_to_ck)}개")
        print(f"  한 complex_key 에 aptSeq 여러 개: {len(multi_seq)} {list(multi_seq.values())[:3]}")
        print(f"  한 aptSeq 에 complex_key 여러 개: {len(multi_ck)}")
        for seq in list(multi_ck)[:3]:
            rows = {
                (r.get("umdNm"), r.get("jibun"), r.get("aptNm")) for r in dev if (r.get("aptSeq") or "").strip() == seq
            }
            print(f"    {seq}: {sorted(rows)}")
    return {"same_common": same_common, "same_keys": kb == kd, "extra": extra}


if __name__ == "__main__":
    load_env()
    dump = Path(os.environ["DUMP"]) if os.environ.get("DUMP") else None
    results = [compare(*a.split(":"), dump) for a in sys.argv[1:]]
    print("\n## 게이트 ①: 상세판이 8개 지문 필드를 같게 주는가")
    print("- 모든 파티션 공통 20개 항목 동일:", all(r["same_common"] for r in results))
    print("- 모든 파티션 지문 집합 동일:", all(r["same_keys"] for r in results))
