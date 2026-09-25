{#
  서빙용 현재 거래 (월 단위 증분). 해제 거래 포함 — 통계에서만 제외한다.
  ㎡당 가격 = price_manwon / area_m2 (만원/㎡). 월 전국 분포 상하위 0.1% 는 is_outlier 로 표시하고
  지역 분위수에서 제외한다 (기획서 7장).
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with cur as (
    select *
    from {{ source('silver', 'apt_trade') }}
    where is_current and {{ deal_ym_filter() }}
),
priced as (
    select deal_ym, cast(price_manwon as double) / cast(area_m2 as double) as ppm2
    from cur
    where not is_cancelled and area_m2 > 0
),
bounds as (
    {{ exact_percentiles('priced', ['deal_ym'], 'ppm2', {'lo': 0.001, 'hi': 0.999}) }}
)
select
    c.trade_id, c.trade_key, c.dup_seq, c.version, c.valid_from, c.missing_since,
    c.complex_key, c.sgg_cd, c.deal_ym, c.deal_date, c.umd_nm, c.apt_nm, c.jibun,
    c.area_m2, c.floor, c.price_manwon, c.build_year,
    case when c.area_m2 > 0 then cast(c.price_manwon as double) / cast(c.area_m2 as double) end as ppm2,
    c.is_cancelled, c.cancel_date, c.registered_date, c.apt_dong, c.deal_kind, c.seller_type, c.buyer_type,
    coalesce(
        not c.is_cancelled and c.area_m2 > 0
        and (cast(c.price_manwon as double) / cast(c.area_m2 as double) < b.lo
             or cast(c.price_manwon as double) / cast(c.area_m2 as double) > b.hi),
        false) as is_outlier
from cur c
left join bounds b on b.deal_ym = c.deal_ym
