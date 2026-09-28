"""`.env` 가 없으면 .env.example 로 만들고, 비어 있는 내부 비밀값을 무작위로 채운다.

외부 API 키(DATA_GO_KR_KEY, REB_API_KEY)는 채우지 않는다 — 사람이 직접 넣는다.
기존 값은 절대 덮어쓰지 않는다 (멱등). 값은 출력하지 않는다.
"""
import secrets
import shutil
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"DATA_GO_KR_KEY", "REB_API_KEY"}
EXTERNAL = REQUIRED | {"VWORLD_API_KEY", "VWORLD_DOMAIN"}  # V-World 는 지도 경계용(선택)
GENERATED_SUFFIXES = ("_PASSWORD", "_SECRET", "_PEPPER", "_SIGNING_KEY", "_ENCRYPTION_KEY")
KEY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # api/src/aptlake_api/keys.py 와 같은 형식


def api_key() -> str:
    """al_live_<keyId 12자>.<secret 43자> — 웹 프록시(BFF) 전용 키."""
    return "al_live_" + "".join(secrets.choice(KEY_ALPHABET) for _ in range(12)) + "." + secrets.token_urlsafe(32)


def main() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists():
        shutil.copy(example, env)
    env.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 비밀값을 쓰기 전에 권한부터 좁힌다
    lines = env.read_text().splitlines()
    present = {l.split("=", 1)[0] for l in lines if "=" in l and not l.startswith("#")}
    # .env.example 에 새로 생긴 변수는 뒤에 덧붙인다
    for l in example.read_text().splitlines():
        if "=" in l and not l.startswith("#") and l.split("=", 1)[0] not in present:
            lines.append(l)
    filled, out = [], []
    for l in lines:
        if "=" in l and not l.startswith("#"):
            k, v = l.split("=", 1)
            if not v.strip() and k == "WEB_API_KEY":
                l = f"{k}={api_key()}"
                filled.append(k)
            elif not v.strip() and k not in EXTERNAL and k.endswith(GENERATED_SUFFIXES):
                l = f"{k}={secrets.token_urlsafe(32)}"
                filled.append(k)
        out.append(l)
    env.write_text("\n".join(out) + "\n")
    empty = {l.split("=", 1)[0] for l in out if "=" in l and not l.startswith("#") and not l.split("=", 1)[1].strip()}
    msg = f".env: generated {len(filled)} secrets"
    if empty & REQUIRED:
        msg += f"; 직접 채워야 할 키: {', '.join(sorted(empty & REQUIRED))}"
    if empty & (EXTERNAL - REQUIRED):
        msg += f"; 선택(지도 경계): {', '.join(sorted(empty & (EXTERNAL - REQUIRED)))}"
    print(msg)


if __name__ == "__main__":
    main()
