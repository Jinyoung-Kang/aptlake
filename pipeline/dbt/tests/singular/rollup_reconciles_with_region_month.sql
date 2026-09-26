{#
  대조 테스트: 전국·시도 집계의 건수는 시군구 집계(region_month)의 합과 정확히 같아야 한다.
  한 행이라도 나오면 실패 → dbt build 실패 → 같은 실행의 ClickHouse 발행이 막힌다.
#}
with r as (
    select deal_ym, substr(sgg_cd, 1, 2) as sido_cd, reported, trades, cancelled
    from {{ ref('region_month') }}
    where deal_ym = coalesce(nullif('{{ var('deal_ym') }}', ''), deal_ym)
),
expected as (
    select sido_cd as region_id, deal_ym, sum(reported) as reported, sum(trades) as trades, sum(cancelled) as cancelled
    from r group by sido_cd, deal_ym
    union all
    select '00', deal_ym, sum(reported), sum(trades), sum(cancelled) from r group by deal_ym
),
actual as (
    select region_id, deal_ym, reported, trades, cancelled
    from {{ ref('region_rollup_month') }}
    where deal_ym = coalesce(nullif('{{ var('deal_ym') }}', ''), deal_ym)
)
select coalesce(e.region_id, a.region_id) as region_id, coalesce(e.deal_ym, a.deal_ym) as deal_ym,
       e.reported as expected_reported, a.reported as actual_reported
from expected e
full outer join actual a on a.region_id = e.region_id and a.deal_ym = e.deal_ym
where e.region_id is null or a.region_id is null
   or e.reported <> a.reported or e.trades <> a.trades or e.cancelled <> a.cancelled
