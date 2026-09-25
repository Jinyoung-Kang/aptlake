"""컨테이너 시작 시 정리 (dagster dev 재시작 = 진행 중이던 실행 프로세스 소멸).

1) STARTED/STARTING 으로 남은 실행 → FAILURE 표시.
   DefaultRunLauncher 는 실행 프로세스 상태 점검을 지원하지 않아 run monitoring 만으로는 감지되지 않고,
   큐(동시 1개)를 막는다. 해당 파티션은 FETCHING 상태가 1시간 뒤 센서에 의해 다시 수집된다.
2) 끝난 실행이 잡고 있거나 대기 중인 동시성 풀 슬롯 → 반납.
   실패 표시만으로는 슬롯이 반납되지 않고, 반납된 슬롯이 죽은 실행의 대기 단계에 다시 배정될 수도 있다.
"""

from dagster import DagsterInstance, DagsterRunStatus, RunsFilter

TERMINAL = {DagsterRunStatus.SUCCESS, DagsterRunStatus.FAILURE, DagsterRunStatus.CANCELED}


def free_slots_of_finished_runs(instance: DagsterInstance) -> int:
    storage = instance.event_log_storage
    run_ids: set[str] = set()
    for key in storage.get_concurrency_keys():
        info = storage.get_concurrency_info(key)
        run_ids |= {s.run_id for s in info.claimed_slots}
        run_ids |= {p.run_id for p in info.pending_steps}
    freed = 0
    for run_id in run_ids:
        run = instance.get_run_by_id(run_id)
        if run is None or run.status in TERMINAL:
            storage.free_concurrency_slots_for_run(run_id)
            freed += 1
    return freed


def main() -> None:
    instance = DagsterInstance.get()
    stale = instance.get_runs(filters=RunsFilter(statuses=[DagsterRunStatus.STARTED, DagsterRunStatus.STARTING]))
    for run in stale:
        instance.report_run_failed(run, message="orphaned by dagster container restart")
    freed = free_slots_of_finished_runs(instance)
    print(f"startup: failed {len(stale)} orphaned runs, freed pool slots of {freed} finished runs")


if __name__ == "__main__":
    main()
