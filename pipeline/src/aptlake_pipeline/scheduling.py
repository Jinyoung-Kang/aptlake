"""수집 센서의 실행 계획 — 순수 함수 (Dagster·DB·Redis 를 모름).

입력: 기한이 된 (월, 상태, 시군구 목록), 진행 중인 실행, 오늘 사용량, 우선순위별 상한, 발행이 밀린 달
출력: 이번 틱에 요청할 실행 목록 (월, 우선순위, 시군구, 원천 호출 여부)

규칙
  - 순서: 증분(최근 3개월) > 재시도 > 재확인(1년 안) > 백필, 같은 등급이면 최신 달이 먼저.
    재시도가 섞인 달은 그 등급에서 한 단계 앞으로 (증분은 이미 맨 앞)
  - 예산: 원천 호출 1회 = (시군구, 월) 1개. 오늘 사용량 + 진행 중 실행의 예상 호출 + 이번에 계획한 호출이
    그 우선순위의 상한을 넘으면 그 달은 이번 틱에서 건너뛴다 (뒤의 작은 달은 들어갈 수 있다)
  - 진행 중인 달은 다시 요청하지 않는다
  - silver 에는 반영됐지만 발행되지 않은 달은 원천 호출 없이 하위 단계만 (예산 0)
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .budget import Ceilings

_RANK = {"incremental": 0, "recheck": 2, "backfill": 3}


@dataclass(frozen=True)
class Planned:
    ym: str
    priority: str  # incremental | retry | recheck | backfill | publish-only
    sgg_codes: tuple[str, ...] = ()
    fetch: bool = True


def priority_for(ym: str, today: dt.date) -> str:
    age = (today.year - int(ym[:4])) * 12 + (today.month - int(ym[4:]))
    return "incremental" if age < 3 else "recheck" if age < 12 else "backfill"


def _fetch_config(run_config: Mapping[str, Any]) -> Mapping[str, Any]:
    ops = run_config.get("ops")
    cfg = ops.get("bronze__rtms_raw", {}).get("config", {}) if isinstance(ops, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def run_calls(run_config: Mapping[str, Any], n_leaf: int) -> int:
    """큐에 있는 실행이 쓸 원천 호출 수 (발행 전용 실행은 0, 시군구 목록이 없으면 전체)."""
    cfg = _fetch_config(run_config)
    if cfg.get("fetch") is False:
        return 0
    return len(list(cfg.get("sgg_codes") or [])) or n_leaf


def plan_runs(
    due: Iterable[Mapping[str, Any]],
    *,
    today: dt.date,
    in_flight: set[str | None],
    used: int,
    queued_calls: int,
    ceilings: Ceilings,
    needing_publish: Iterable[str],
    partition_keys: Iterable[str],
) -> list[Planned]:
    by_month: dict[str, dict[str, Any]] = {}
    for r in due:
        m = by_month.setdefault(r["deal_ym"], {"sgg": set(), "retry": False})
        m["sgg"].update(r["sgg"])
        m["retry"] |= r["status"] == "RETRY"
    order = sorted(
        by_month.items(),
        key=lambda kv: (_RANK[priority_for(kv[0], today)] - (1 if kv[1]["retry"] else 0), -int(kv[0])),
    )
    planned = used + queued_calls
    out: list[Planned] = []
    for ym, info in order:
        if ym in in_flight:
            continue
        prio = priority_for(ym, today)
        n = len(info["sgg"])
        if planned + n > ceilings.for_priority(prio):
            continue
        planned += n
        tag = "retry" if info["retry"] and prio != "incremental" else prio
        out.append(Planned(ym, tag, tuple(sorted(info["sgg"]))))
    requested = {p.ym for p in out}
    keys = set(partition_keys)
    for ym in needing_publish:
        if ym in in_flight or ym in requested or ym not in keys:
            continue
        out.append(Planned(ym, "publish-only", fetch=False))
    return out
