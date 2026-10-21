"""dims_and_index 잡 설정 (ADR-052). dbt manifest 가 필요하다: CI 는 dbt parse 뒤에 테스트를 돌린다."""

from aptlake_pipeline import definitions as d


def test_dims_and_index_runs_one_step_at_a_time():
    """지수 산출이 dbt 단계와 겹치면 컨테이너 한도를 넘는다 — 스케줄·make index 모두 잡 기본 설정으로 돈다."""
    job = d.defs.resolve_job_def("dims_and_index")
    assert job.run_config == {"execution": {"config": {"multiprocess": {"max_concurrent": 1}}}}
