{# 전월세 계약 버전 이력 (SCD2, ADR-050) — 현재행 + 닫힌 과거 버전. 바뀌는 원천 값(속성 해시 재료) 전부 #}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

select rent_id, deal_ym, deal_date, version, valid_from, valid_to, is_current,
       contract_type, contract_term, use_rr_right, pre_deposit_manwon, pre_monthly_rent_manwon, build_year
from {{ source('silver', 'apt_rent') }}
where {{ deal_ym_filter() }}
