"""W1 스파이크: 실거래 API 응답 형식·페이지 크기·조회 가능 기간을 측정한다 (키는 출력하지 않음)."""
import os, sys, time, urllib.parse, urllib.request, re
from pathlib import Path

def load_env(path=Path(__file__).resolve().parents[1] / ".env"):
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"

def call(lawd, ym, page=1, rows=10):
    q = urllib.parse.urlencode({"serviceKey": os.environ["DATA_GO_KR_KEY"], "LAWD_CD": lawd,
                                "DEAL_YMD": ym, "pageNo": page, "numOfRows": rows})
    t = time.perf_counter()
    with urllib.request.urlopen(f"{URL}?{q}", timeout=30) as r:
        body = r.read().decode("utf-8")
    return body, (time.perf_counter() - t) * 1000

def tag(body, name):
    m = re.search(rf"<{name}>(.*?)</{name}>", body, re.S)
    return m.group(1) if m else None

if __name__ == "__main__":
    load_env()
    for lawd, ym, rows in [(a.split(":")[0], a.split(":")[1], int(a.split(":")[2])) for a in sys.argv[1:]]:
        body, ms = call(lawd, ym, rows=rows)
        print(f"{lawd} {ym} rows={rows} code={tag(body,'resultCode')} msg={tag(body,'resultMsg')} "
              f"total={tag(body,'totalCount')} items={body.count('<item>')} bytes={len(body)} {ms:.0f}ms")
        if os.environ.get("DUMP"):
            Path(os.environ["DUMP"]).write_text(body)
