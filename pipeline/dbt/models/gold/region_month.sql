{#
  시군구 × 계약월 통계 (FR-401).
    reported  = 현재행 수 (신고 건수)       trades = 해제 제외 건수     cancelled = 해제 건수
    priced    = 분위수 표본 (해제·면적 0·이상치 제외)
    p25/median/p75 = 정확 분위수 (만원/㎡). 표본 < 5 면 null + low_sample (기획서 7장)
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with t as (
    select * from {{ ref('trade_serving') }} where {{ deal_ym_filter() }}
),
counts as (
    select sgg_cd, deal_ym,
           count(*) as reported,
           count_if(not is_cancelled) as trades,
           count_if(is_cancelled) as cancelled,
           count_if(not is_cancelled and area_m2 > 0 and not is_outlier) as priced,
           count_if(is_outlier) as outliers
    from t
    group by sgg_cd, deal_ym
),
sample as (
    select sgg_cd, deal_ym, ppm2 from t
    where not is_cancelled and area_m2 > 0 and not is_outlier
),
pct as (
    {{ exact_percentiles('sample', ['sgg_cd', 'deal_ym'], 'ppm2', {'p25': 0.25, 'p50': 0.5, 'p75': 0.75}) }}
)
select
    c.sgg_cd, c.deal_ym,
    cast(c.reported as integer) as reported,
    cast(c.trades as integer) as trades,
    cast(c.cancelled as integer) as cancelled,
    cast(c.priced as integer) as priced,
    cast(c.outliers as integer) as outliers,
    case when c.priced >= 5 then p.p25 end as p25_ppm2,
    case when c.priced >= 5 then p.p50 end as median_ppm2,
    case when c.priced >= 5 then p.p75 end as p75_ppm2,
    c.priced < 5 as low_sample
from counts c
left join pct p on p.sgg_cd = c.sgg_cd and p.deal_ym = c.deal_ym
