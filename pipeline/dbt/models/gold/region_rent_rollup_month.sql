{#
  시도·전국 × 계약월 전월세 통계 (region_rent_month 와 같은 정의, ADR-050).
  분위수는 시군구 값을 합칠 수 없어 계약 단위에서 다시 정확 분위수를 계산한다. region_id: 시도 2자리, 전국 '00'.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with t as (
    select substr(sgg_cd, 1, 2) as sido_cd, * from {{ ref('rent_serving') }} where {{ deal_ym_filter() }}
),
expanded as (
    select sido_cd as region_id, deal_ym, is_jeonse, area_m2, is_outlier, deposit_ppm2, deposit_manwon,
           monthly_rent_manwon, contract_type, use_rr_right
    from t
    union all
    select '00' as region_id, deal_ym, is_jeonse, area_m2, is_outlier, deposit_ppm2, deposit_manwon,
           monthly_rent_manwon, contract_type, use_rr_right
    from t
),
counts as (
    select region_id, deal_ym,
           count(*) as contracts,
           count_if(is_jeonse) as jeonse,
           count_if(not is_jeonse) as wolse,
           count_if(contract_type = '갱신') as renewals,
           count_if(contract_type = '신규') as new_contracts,
           count_if(use_rr_right = '사용') as rr_used,
           count_if(is_jeonse and area_m2 > 0 and not is_outlier) as jeonse_priced,
           count_if(is_outlier) as outliers
    from expanded
    group by region_id, deal_ym
),
jeonse_sample as (
    select region_id, deal_ym, deposit_ppm2 from expanded where is_jeonse and area_m2 > 0 and not is_outlier
),
jp as (
    {{ exact_percentiles('jeonse_sample', ['region_id', 'deal_ym'], 'deposit_ppm2', {'p25': 0.25, 'p50': 0.5, 'p75': 0.75}) }}
),
wolse_sample as (
    select region_id, deal_ym, cast(monthly_rent_manwon as double) as rent, cast(deposit_manwon as double) as deposit
    from expanded where not is_jeonse
),
wr as (
    {{ exact_percentiles('wolse_sample', ['region_id', 'deal_ym'], 'rent', {'p50': 0.5}) }}
),
wd as (
    {{ exact_percentiles('wolse_sample', ['region_id', 'deal_ym'], 'deposit', {'p50': 0.5}) }}
)
select
    c.region_id,
    case when c.region_id = '00' then 'nation' else 'sido' end as level,
    c.deal_ym,
    cast(c.contracts as integer) as contracts,
    cast(c.jeonse as integer) as jeonse,
    cast(c.wolse as integer) as wolse,
    cast(c.renewals as integer) as renewals,
    cast(c.new_contracts as integer) as new_contracts,
    cast(c.rr_used as integer) as rr_used,
    cast(c.jeonse_priced as integer) as jeonse_priced,
    cast(c.outliers as integer) as outliers,
    case when c.jeonse_priced >= 5 then jp.p25 end as jeonse_p25_ppm2,
    case when c.jeonse_priced >= 5 then jp.p50 end as jeonse_median_ppm2,
    case when c.jeonse_priced >= 5 then jp.p75 end as jeonse_p75_ppm2,
    case when c.wolse >= 5 then wr.p50 end as wolse_median_rent,
    case when c.wolse >= 5 then wd.p50 end as wolse_median_deposit,
    c.jeonse_priced < 5 as low_sample
from counts c
left join jp on jp.region_id = c.region_id and jp.deal_ym = c.deal_ym
left join wr on wr.region_id = c.region_id and wr.deal_ym = c.deal_ym
left join wd on wd.region_id = c.region_id and wd.deal_ym = c.deal_ym
