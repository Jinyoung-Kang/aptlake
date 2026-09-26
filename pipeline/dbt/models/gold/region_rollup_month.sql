{#
  시도·전국 × 계약월 통계 (region_month 와 같은 정의).
  시도·전국 분위수는 시군구 분위수를 합칠 수 없으므로 거래 단위에서 다시 정확 분위수를 계산한다.
    region_id: 시도 2자리 (법정동코드 앞 2자리), 전국 = '00'
  이상치 기준(월 전국 상하위 0.1%)은 trade_serving 의 is_outlier 를 그대로 쓴다.
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with t as (
    select substr(sgg_cd, 1, 2) as sido_cd, * from {{ ref('trade_serving') }} where {{ deal_ym_filter() }}
),
expanded as (
    select sido_cd as region_id, deal_ym, is_cancelled, area_m2, is_outlier, ppm2 from t
    union all
    select '00' as region_id, deal_ym, is_cancelled, area_m2, is_outlier, ppm2 from t
),
counts as (
    select region_id, deal_ym,
           count(*) as reported,
           count_if(not is_cancelled) as trades,
           count_if(is_cancelled) as cancelled,
           count_if(not is_cancelled and area_m2 > 0 and not is_outlier) as priced,
           count_if(is_outlier) as outliers
    from expanded
    group by region_id, deal_ym
),
sample as (
    select region_id, deal_ym, ppm2 from expanded
    where not is_cancelled and area_m2 > 0 and not is_outlier
),
pct as (
    {{ exact_percentiles('sample', ['region_id', 'deal_ym'], 'ppm2', {'p25': 0.25, 'p50': 0.5, 'p75': 0.75}) }}
)
select
    c.region_id,
    case when c.region_id = '00' then 'nation' else 'sido' end as level,
    c.deal_ym,
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
left join pct p on p.region_id = c.region_id and p.deal_ym = c.deal_ym
