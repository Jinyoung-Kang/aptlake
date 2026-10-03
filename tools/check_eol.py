"""런타임·서비스 이미지의 지원 종료(EOL) 점검 — 주 1회 CI(.github/workflows/eol.yml)와 수동 실행.

docker-compose.yml·Dockerfile 에 고정한 버전을 읽어 endoflife.date 의 지원 종료일과 비교한다.
  지원 종료가 지났으면 실패(종료 코드 1), 60일 안이면 경고. Dependabot 이 큰·LTS 변경을 제안하지 않도록 막아 둔
  ClickHouse·Prometheus 같은 서비스가 조용히 지원 종료되는 것을 잡으려고 만들었다 (2026-10 점검 H1·H2).
표준 라이브러리만 쓴다:  python3 tools/check_eol.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WARN_DAYS = 60

# 이미지 이름 → (endoflife.date 제품, 태그에서 주기(cycle)를 뽑는 정규식)
RULES: dict[str, tuple[str, str]] = {
    "clickhouse/clickhouse-server": ("clickhouse", r"^(\d+\.\d+)"),
    "prom/prometheus": ("prometheus", r"^v?(\d+\.\d+)"),
    "grafana/grafana": ("grafana", r"^(\d+\.\d+)"),
    "postgres": ("postgresql", r"^(\d+)"),
    "redis": ("redis", r"^(\d+\.\d+)"),
    "python": ("python", r"^(\d+\.\d+)"),
    "node": ("nodejs", r"^(\d+)"),
    "nginxinc/nginx-unprivileged": ("nginx", r"^(\d+\.\d+)"),
}
DEBIAN = {"bookworm": "12", "trixie": "13"}


def images() -> set[str]:
    found: set[str] = set()
    for f in [ROOT / "docker-compose.yml", *ROOT.glob("*/Dockerfile")]:
        for m in re.finditer(r"(?:image:|FROM)\s+([\w./-]+):([\w.-]+)", f.read_text()):
            found.add(f"{m.group(1)}:{m.group(2)}")
    return found


def cycle_info(product: str, cycle: str) -> dict:
    url = f"https://endoflife.date/api/{product}/{cycle}.json"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "aptlake-eol-check"}), timeout=20
    ) as r:
        return json.load(r)


def main() -> int:
    today = dt.date.today()
    rows, failed, warned = [], 0, 0
    checks: list[tuple[str, str, str]] = []
    for ref in sorted(images()):
        name, tag = ref.rsplit(":", 1)
        if name in RULES:
            product, pattern = RULES[name]
            if m := re.match(pattern, tag):
                checks.append((ref, product, m.group(1)))
        if name == "python":  # 파이썬 이미지는 바탕 Debian 도 본다
            for code, ver in DEBIAN.items():
                if code in tag:
                    checks.append((ref, "debian", ver))
    for ref, product, cycle in checks:
        try:
            info = cycle_info(product, cycle)
        except Exception as e:  # noqa: BLE001 — 조회 실패는 실패로 숨기지 않고 표에 남긴다
            rows.append((ref, f"{product} {cycle}", "조회 실패", type(e).__name__))
            warned += 1
            continue
        eol = info.get("eol")
        if eol in (False, None):
            rows.append((ref, f"{product} {cycle}", "지원 중", "종료일 미정"))
            continue
        if eol is True:
            rows.append((ref, f"{product} {cycle}", "지원 종료", "날짜 미상"))
            failed += 1
            continue
        end = dt.date.fromisoformat(str(eol))
        left = (end - today).days
        state = "지원 종료" if left < 0 else f"{left}일 남음" if left <= WARN_DAYS else "지원 중"
        failed += left < 0
        warned += 0 <= left <= WARN_DAYS
        rows.append((ref, f"{product} {cycle}", state, str(end)))

    lines = ["| 이미지 | 제품·주기 | 상태 | 지원 종료일 |", "|---|---|---|---|"]
    lines += [f"| `{a}` | {b} | {c} | {d} |" for a, b, c, d in rows]
    report = "\n".join(lines)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        Path(summary).write_text("## 지원 종료(EOL) 점검\n\n" + report + "\n")
    print(f"\n지원 종료 {failed}건, {WARN_DAYS}일 안 종료·조회 실패 {warned}건", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
