{# 거래 버전 이력 (SCD2, FR-203) — 현재행 + 닫힌 과거 버전 #}
{{ config(
    materialized='incremental',
    incremental_strategy='delete+insert',
    unique_key='deal_ym',
    properties={"partitioning": "ARRAY['deal_ym']"}
) }}

select trade_id, deal_ym, deal_date, version, valid_from, valid_to, is_current,
       is_cancelled, cancel_date, registered_date, apt_dong, deal_kind, seller_type, buyer_type
from {{ source('silver', 'apt_trade') }}
where {{ deal_ym_filter() }}
