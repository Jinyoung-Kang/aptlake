#!/usr/bin/env bash
# Lighthouse 측정 (QA 스택 웹): 주요 화면 × 모바일·데스크톱 → 점수·핵심 지표 표. --assert-cls 면 CLS > 0.1 인 화면이 있을 때 실패 (QA-013 재현 시험)
#   QA_TOOLS=<lighthouse 설치 폴더> qa/probes/lighthouse.sh <결과 폴더> [--assert-cls]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="$1"; ASSERT="${2:-}"; mkdir -p "$OUT"
CX=$(curl -s "http://127.0.0.1:8710/v1/regions/11110/complexes?from=2025-01&to=2025-12&limit=1" | python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['complexKey'])")
for ff in mobile desktop; do
  for pg in "market" "region/11110" "trades?sgg=11110" "complex/$CX" "quality"; do
    n=$(echo "$pg" | tr '/?=' '___' | cut -c1-30); extra=(); [ "$ff" = desktop ] && extra=(--preset=desktop)
    CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" "$QA_TOOLS/node_modules/.bin/lighthouse" "http://127.0.0.1:3710/#/$pg" ${extra[@]+"${extra[@]}"} \
      --quiet --chrome-flags="--headless=new" --only-categories=performance,accessibility,best-practices --output=json --output-path="$OUT/${ff}_$n.json" >/dev/null 2>&1
  done
done
python3 - "$OUT" "$ASSERT" <<'PY'
import glob, json, os, sys
bad = []
print("화면 | 성능 | 접근성 | 모범 사례 | FCP | LCP | TBT | CLS")
for f in sorted(glob.glob(os.path.join(sys.argv[1], "*.json"))):
    d = json.load(open(f)); c = d["categories"]; a = d["audits"]
    cls = a["cumulative-layout-shift"]["numericValue"]
    print(" | ".join([os.path.basename(f)[:-5], *(str(round(c[k]["score"] * 100)) for k in ("performance", "accessibility", "best-practices")),
                      *(a[k]["displayValue"] for k in ("first-contentful-paint", "largest-contentful-paint", "total-blocking-time")), f"{cls:.3f}"]))
    if cls > 0.1:
        bad.append(os.path.basename(f)[:-5])
if sys.argv[2] == "--assert-cls":
    print(f"CLS > 0.1: {len(bad)}개 {bad}")
    sys.exit(1 if bad else 0)
PY
