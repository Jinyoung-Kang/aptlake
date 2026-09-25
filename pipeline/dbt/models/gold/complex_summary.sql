{# 단지 차원 + 현재 유효 거래 수 (전체 재생성, 소형) #}
{{ config(materialized='table') }}

select c.complex_key, c.sgg_cd, c.umd_nm, c.jibun, c.apt_nm, c.build_year,
       coalesce(c.land_leasehold, false) as land_leasehold, c.first_seen,
       cast(coalesce(t.trades, 0) as integer) as trades
from {{ source('silver', 'apt_complex') }} c
left join (
    select complex_key, count(*) as trades
    from {{ source('silver', 'apt_trade') }}
    where is_current and not is_cancelled
    group by complex_key
) t on t.complex_key = c.complex_key
