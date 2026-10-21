{# 단지 차원 + 현재 유효 거래 수 (전체 재생성, 소형).
   공식 시군구 목록(silver.region)에 있는 단지만 — 거래 표는 발행된 달 파티션으로만 서빙되지만 이 차원은 달 구분 없이
   통째로 발행되므로, 범위 밖 코드(통합 테스트의 합성 시군구 등)가 섞이면 그대로 검색에 노출됐다.
   토지임대부 여부는 NULL(미상) 그대로 — 전월세에만 있는 단지는 원천에 이 항목이 없다. false 로 메우면 '아님'이 된다 (ADR-018). #}
{{ config(materialized='table') }}

select c.complex_key, c.sgg_cd, c.umd_nm, c.jibun, c.apt_nm, c.build_year,
       c.land_leasehold, c.first_seen,
       cast(coalesce(t.trades, 0) as integer) as trades
from {{ source('silver', 'apt_complex') }} c
left join (
    select complex_key, count(*) as trades
    from {{ source('silver', 'apt_trade') }}
    where is_current and not is_cancelled
    group by complex_key
) t on t.complex_key = c.complex_key
where c.sgg_cd in (select sgg_cd from {{ source('silver', 'region') }})
