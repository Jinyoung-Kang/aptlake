"""QA 스택 전용 비밀값 파일을 만든다 (운영 .env 는 읽지도 쓰지도 않는다). 값은 출력하지 않는다.

.env.example 의 변수를 그대로 쓰고, 내부 비밀값은 무작위로 채운다. 외부 API 키는 QA 에서 원천을 부르지 않으므로
알아볼 수 있는 더미 값('qa-not-used')을 넣는다 — 실수로 수집이 돌아도 원천이 키를 거부할 뿐 한도를 쓰지 않는다.
"""

import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from init_env import EXTERNAL, GENERATED_SUFFIXES, api_key  # noqa: E402


def main(out: Path) -> None:
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            if k in EXTERNAL:
                line = f"{k}=qa-not-used"
            elif k == "WEB_API_KEY":
                line = f"{k}={api_key()}"
            elif not v.strip() and k.endswith(GENERATED_SUFFIXES):
                line = f"{k}={secrets.token_urlsafe(32)}"
        lines.append(line)
    out.write_text("\n".join(lines) + "\n")
    out.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"{out}: QA 전용 비밀값 생성 (값은 출력하지 않음)")


if __name__ == "__main__":
    main(ROOT / sys.argv[1])
