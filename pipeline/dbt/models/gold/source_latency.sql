{# 원천 반영 지연 (ADR-046, 데이터 제품 명세 4장) — 매일 전체 재생성 (dims_and_index).
   L1 신고 반영: 거래가 처음 관측된 날(버전 1 의 valid_from, KST) − 계약일
   L2 해제 반영: 해제가 처음 관측된 날(is_cancelled 가 처음 참인 버전의 valid_from) − 해제일
   관측 시각은 수집 시각이라 원천 쪽 지연(신고·공개)에 수집 주기가 더해진 값이다.
   제외(reason): initial_load   파티션 첫 수집(초기 적재·백필)에서 이미 있던 것 — 언제부터 있었는지 모른다
                 low_resolution 관측 때 계약월이 3개월 이상 지남 — 30·90일마다 다시 받아 하루 해상도가 아니다
                 time_inverted  이전 버전보다 이른 시각 (ADR-043 D1 이전 데이터)
                 no_base_date   해제일이 없음 / negative_lag 기준일이 관측일보다 늦음 (원천 값 이상)
   입도: (지표, 관측일, 시도, 제외 사유, 지연 일수) → 건수. 분위수는 API 가 이 분포에서 정확히 계산한다. #}
{{ config(materialized='table') }}

with part_first as (
    select sgg_cd, deal_ym, min(fetched_at) as first_fetch
    from {{ source('bronze', 'rtms_raw') }}
    group by sgg_cd, deal_ym
),
v as (
    select trade_id, sgg_cd, deal_ym, deal_date, version, valid_from, is_cancelled, cancel_date,
           lag(valid_from) over (partition by trade_id order by version) as prev_from
    from {{ source('silver', 'apt_trade') }}
    where sgg_cd in (select sgg_cd from {{ source('silver', 'region') }})
),
first_cancel as (
    select *, row_number() over (partition by trade_id order by version) as rn
    from v where is_cancelled
),
ev as (
    select 'L1' as metric, sgg_cd, deal_ym, valid_from, prev_from, deal_date as base_date from v where version = 1
    union all
    select 'L2', sgg_cd, deal_ym, valid_from, prev_from, cancel_date from first_cancel where rn = 1
),
obs as (
    select e.*, p.first_fetch,
           cast(e.valid_from at time zone 'Asia/Seoul' as date) as observed_date
    from ev e join part_first p on p.sgg_cd = e.sgg_cd and p.deal_ym = e.deal_ym
),
cls as (
    select metric, observed_date, substr(sgg_cd, 1, 2) as sido_cd, base_date,
           case
               when prev_from is not null and valid_from < prev_from then 'time_inverted'
               when valid_from <= first_fetch then 'initial_load'
               when base_date is null then 'no_base_date'
               when (year(observed_date) * 12 + month(observed_date))
                    - (cast(substr(deal_ym, 1, 4) as integer) * 12 + cast(substr(deal_ym, 5, 2) as integer)) >= 3
                   then 'low_resolution'
               when base_date > observed_date then 'negative_lag'
               else ''
           end as reason
    from obs
)
select metric, observed_date, sido_cd, reason,
       case when reason = '' then date_diff('day', base_date, observed_date) end as lag_days,
       cast(count(*) as bigint) as n
from cls
group by metric, observed_date, sido_cd, reason,
         case when reason = '' then date_diff('day', base_date, observed_date) end
