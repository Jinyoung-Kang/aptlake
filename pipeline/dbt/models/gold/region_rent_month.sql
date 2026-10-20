{#
  시군구 × 계약월 전월세 통계 (ADR-050).
    contracts = 현재 계약 수       jeonse·wolse = 월세 0 / 그 밖 (jeonse + wolse = contracts)
    renewals·new_contracts = 계약 구분 원문 '갱신'·'신규' 수 (나머지는 원천이 비운 '미상')
    rr_used   = 갱신요구권 원문 '사용' 수
    jeonse_priced = 전세 보증금 ㎡당 분위수 표본 (면적 0·이상치 제외)
    jeonse p25/median/p75 = 전세 보증금 정확 분위수 (만원/㎡). 표본 < 5 면 null + low_sample (매매와 같은 기준)
    wolse_median_rent·wolse_median_deposit = 월세 계약의 월세·보증금 중앙값 (만원). 월세 표본 < 5 면 null
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with t as (
    select * from {{ ref('rent_serving') }} where {{ deal_ym_filter() }}
),
counts as (
    select sgg_cd, deal_ym,
           count(*) as contracts,
           count_if(is_jeonse) as jeonse,
           count_if(not is_jeonse) as wolse,
           count_if(contract_type = '갱신') as renewals,
           count_if(contract_type = '신규') as new_contracts,
           count_if(use_rr_right = '사용') as rr_used,
           count_if(is_jeonse and area_m2 > 0 and not is_outlier) as jeonse_priced,
           count_if(is_outlier) as outliers
    from t
    group by sgg_cd, deal_ym
),
jeonse_sample as (
    select sgg_cd, deal_ym, deposit_ppm2 from t where is_jeonse and area_m2 > 0 and not is_outlier
),
jp as (
    {{ exact_percentiles('jeonse_sample', ['sgg_cd', 'deal_ym'], 'deposit_ppm2', {'p25': 0.25, 'p50': 0.5, 'p75': 0.75}) }}
),
wolse_sample as (
    select sgg_cd, deal_ym, cast(monthly_rent_manwon as double) as rent, cast(deposit_manwon as double) as deposit
    from t where not is_jeonse
),
wr as (
    {{ exact_percentiles('wolse_sample', ['sgg_cd', 'deal_ym'], 'rent', {'p50': 0.5}) }}
),
wd as (
    {{ exact_percentiles('wolse_sample', ['sgg_cd', 'deal_ym'], 'deposit', {'p50': 0.5}) }}
)
select
    c.sgg_cd, c.deal_ym,
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
left join jp on jp.sgg_cd = c.sgg_cd and jp.deal_ym = c.deal_ym
left join wr on wr.sgg_cd = c.sgg_cd and wr.deal_ym = c.deal_ym
left join wd on wd.sgg_cd = c.sgg_cd and wd.deal_ym = c.deal_ym
