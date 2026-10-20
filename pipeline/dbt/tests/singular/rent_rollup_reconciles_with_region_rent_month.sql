{#
  대조 테스트 (전월세, ADR-050): 전국·시도 집계의 건수는 시군구 집계(region_rent_month)의 합과 정확히 같아야 한다.
  한 행이라도 나오면 실패 → dbt build 실패 → 같은 실행의 ClickHouse 발행이 막힌다.
#}
with r as (
    select deal_ym, substr(sgg_cd, 1, 2) as sido_cd, contracts, jeonse, renewals, rr_used
    from {{ ref('region_rent_month') }}
    where deal_ym = coalesce(nullif('{{ var('deal_ym') }}', ''), deal_ym)
),
expected as (
    select sido_cd as region_id, deal_ym, sum(contracts) as contracts, sum(jeonse) as jeonse,
           sum(renewals) as renewals, sum(rr_used) as rr_used
    from r group by sido_cd, deal_ym
    union all
    select '00', deal_ym, sum(contracts), sum(jeonse), sum(renewals), sum(rr_used) from r group by deal_ym
),
actual as (
    select region_id, deal_ym, contracts, jeonse, renewals, rr_used
    from {{ ref('region_rent_rollup_month') }}
    where deal_ym = coalesce(nullif('{{ var('deal_ym') }}', ''), deal_ym)
)
select coalesce(e.region_id, a.region_id) as region_id, coalesce(e.deal_ym, a.deal_ym) as deal_ym,
       e.contracts as expected_contracts, a.contracts as actual_contracts
from expected e
full outer join actual a on a.region_id = e.region_id and a.deal_ym = e.deal_ym
where e.region_id is null or a.region_id is null
   or e.contracts <> a.contracts or e.jeonse <> a.jeonse or e.renewals <> a.renewals or e.rr_used <> a.rr_used
