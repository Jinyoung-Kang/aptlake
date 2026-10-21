#!/bin/bash
# Trino 시작 전에 힙 덤프 자리를 비운다 (ADR-055). 끝에서 이미지의 run-trino 를 exec 해 Java 가 PID 1 로 남는다.
# PID 가 늘 1 이라 덤프 이름도 늘 java_pid1.hprof 다. HotSpot 은 같은 이름이 있으면 덤프하지 않으므로(O_EXCL) 시각 이름으로 옮긴다.
# 정리가 실패해도 Trino 는 떠야 하므로 set -e 를 쓰지 않는다.
d=/home/trino/heapdump
mkdir -p "$d"

# 끊긴 덤프가 남긴 병합 전 조각(.pN)은 쓸모가 없다
rm -f "$d"/java_pid*.hprof.p*
for f in "$d"/java_pid*.hprof; do
  [ -e "$f" ] || continue
  if [ -s "$f" ]; then
    to="$d/oom-$(date -u -r "$f" +%Y%m%dT%H%M%SZ).hprof"
    mv "$f" "$to" && echo "heapdump: $f -> $to"
  else
    rm -f "$f" && echo "heapdump: 빈 파일 삭제 $f"
  fi
done
# 최근 둘만 남긴다 (하나에 약 1GB)
ls -1t "$d"/oom-*.hprof 2>/dev/null | tail -n +3 | xargs -r rm -f --

exec /usr/lib/trino/bin/run-trino "$@"
