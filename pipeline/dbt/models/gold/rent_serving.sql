{#
  서빙용 현재 전월세 계약 (월 단위 증분, ADR-050). 금액 단위는 만원 — 매매(price_manwon)와 같다.
  전세 = 월세가 0 인 계약 (정의로 밝혀 쓴다: 원천 monthlyRent 는 빈 값 없이 늘 숫자 — 0단계 탐침). 나머지는 월세(보증부 포함).
  보증금 ㎡당 = deposit_manwon / area_m2 (만원/㎡). 전세 계약의 월 전국 분포 상하위 0.1% 는 is_outlier 로 표시하고
  지역 분위수에서 뺀다 (매매 trade_serving 과 같은 규칙). 월세 계약은 보증금·월세 조합이 다양해 표시하지 않는다.
  계약 구분(contract_type)·갱신요구권(use_rr_right)은 원천 원문 그대로 ('신규'·'갱신', '사용', 빈 값 = 미상).
#}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

with cur as (
    select *
    from {{ source('silver', 'apt_rent') }}
    where is_current and {{ deal_ym_filter() }}
),
jeonse as (
    select deal_ym, cast(deposit_manwon as double) / cast(area_m2 as double) as ppm2
    from cur
    where monthly_rent_manwon = 0 and area_m2 > 0
),
bounds as (
    {{ exact_percentiles('jeonse', ['deal_ym'], 'ppm2', {'lo': 0.001, 'hi': 0.999}) }}
)
select
    c.rent_id, c.rent_key, c.dup_seq, c.version, c.valid_from, c.missing_since,
    c.complex_key, c.sgg_cd, c.deal_ym, c.deal_date, c.umd_nm, c.apt_nm, c.jibun,
    c.area_m2, c.floor, c.build_year,
    c.deposit_manwon, c.monthly_rent_manwon,
    c.monthly_rent_manwon = 0 as is_jeonse,
    case when c.area_m2 > 0 then cast(c.deposit_manwon as double) / cast(c.area_m2 as double) end as deposit_ppm2,
    c.contract_type, c.contract_term, c.use_rr_right, c.pre_deposit_manwon, c.pre_monthly_rent_manwon,
    coalesce(
        c.monthly_rent_manwon = 0 and c.area_m2 > 0
        and (cast(c.deposit_manwon as double) / cast(c.area_m2 as double) < b.lo
             or cast(c.deposit_manwon as double) / cast(c.area_m2 as double) > b.hi),
        false) as is_outlier
from cur c
left join bounds b on b.deal_ym = c.deal_ym
