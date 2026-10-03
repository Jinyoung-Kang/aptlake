#!/usr/bin/env bash
# 이미지 취약점 점검 — Trivy 로 HIGH·CRITICAL 중 수정판이 있는 것만 본다 (QA-005). 하나라도 남으면 종료 코드 1.
#   tools/scan_images.sh               자체 이미지 + 서드파티 이미지
#   tools/scan_images.sh own           자체 이미지(로컬 빌드본 aptlake-*:local)만 — 예외 없이 0건이어야 한다 (CI images 작업과 같은 기준)
#                                      QA 스택 빌드본은 IMAGE_PREFIX=aptlake-qa
#   tools/scan_images.sh third-party   docker-compose.yml 에 고정한 서드파티 이미지만 — infra/trivy/<이미지>.yaml 의 예외를 뺀다
# 서드파티 예외는 이미지마다 파일 하나, 항목마다 닿지 않는 이유와 만료일을 적는다. 만료일이 지나거나 새 항목이 생기면 다시 실패한다.
# 서드파티는 레지스트리에서 바로 읽어(--image-src remote) 로컬 이미지 태그를 바꾸지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
TRIVY=aquasec/trivy:0.67.2
PREFIX="${IMAGE_PREFIX:-aptlake}"
CACHE="${TRIVY_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/aptlake-trivy}"
mkdir -p "$CACHE"
TEMPLATE='{{range .}}{{range .Vulnerabilities}}  {{.Severity}} {{.VulnerabilityID}} {{.PkgName}} {{.InstalledVersion}} → {{.FixedVersion}}{{"\n"}}{{end}}{{end}}'
fail=0

trivy() {  # $1 이미지 디렉터리(/in 으로 마운트), 나머지는 trivy image 인자
  local dir=$1; shift
  docker run --rm -v "$CACHE:/root/.cache/trivy" -v "$PWD/infra/trivy:/ignore:ro" -v "$dir:/in:ro" "$TRIVY" image -q \
    --severity HIGH,CRITICAL --ignore-unfixed --format template --template "$TEMPLATE" "$@"
}

report() {  # $1 이미지, $2 결과
  if [ -n "$2" ]; then
    echo "✗ $1 — $(grep -c . <<<"$2")건"
    echo "$2"
    fail=1
  else
    echo "✓ $1 — 0건"
  fi
}

own() {
  local tmp out img
  tmp=$(mktemp -d)
  for img in "$PREFIX-api:local" "$PREFIX-pipeline:local" "$PREFIX-web:local"; do
    if ! docker image inspect "$img" >/dev/null 2>&1; then
      echo "✗ $img — 이미지 없음 (먼저 빌드: docker compose build --pull)"
      fail=1
      continue
    fi
    docker save -o "$tmp/image.tar" "$img"
    out=$(trivy "$tmp" --input /in/image.tar)
    rm -f "$tmp/image.tar"
    report "$img" "$out"
  done
  rmdir "$tmp"
}

third_party() {
  local img name args out
  for img in $(sed -n 's/^ *image: *//p' docker-compose.yml | grep -v '^aptlake-' | sort -u); do
    name=${img%%:*}
    name=${name##*/}
    args=(--image-src remote)
    [ -f "infra/trivy/$name.yaml" ] && args+=(--ignorefile "/ignore/$name.yaml")
    out=$(trivy "$PWD/infra/trivy" "${args[@]}" "$img")
    report "$img" "$out"
  done
}

case "${1:-all}" in
  own) own ;;
  third-party) third_party ;;
  all) own; third_party ;;
  *) echo "사용법: $0 [own|third-party]" >&2; exit 2 ;;
esac
exit $fail
