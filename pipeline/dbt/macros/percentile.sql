{#
  정확한 분위수 (선형 보간, numpy.percentile 기본값과 같은 정의).
  Trino approx_percentile 은 근사값이라, 서빙 계층과 결과를 대조(FR-401 수용 기준)할 수 있도록 정확값을 쓴다.
    h = (n - 1) * p,  값 = v[floor(h)] + (h - floor(h)) * (v[ceil(h)] - v[floor(h)])   (v 는 0 기반 정렬)
  입력 relation 은 value_col, group_cols 를 가져야 한다.
#}
{% macro exact_percentiles(relation, group_cols, value_col, pcts) %}
  with ranked as (
    select {{ group_cols | join(', ') }}, {{ value_col }} as v,
           row_number() over (partition by {{ group_cols | join(', ') }} order by {{ value_col }}) - 1 as r,
           count(*) over (partition by {{ group_cols | join(', ') }}) as n
    from {{ relation }}
  ), picked as (
    select {{ group_cols | join(', ') }}, max(n) as n
    {%- for name, p in pcts.items() %}
      , max(case when r = cast(floor((n - 1) * {{ p }}) as bigint) then v end) as {{ name }}_lo
      , max(case when r = cast(ceil((n - 1) * {{ p }}) as bigint) then v end) as {{ name }}_hi
    {%- endfor %}
    from ranked
    group by {{ group_cols | join(', ') }}
  )
  select {{ group_cols | join(', ') }}, n
  {%- for name, p in pcts.items() %}
    , {{ name }}_lo + (((n - 1) * {{ p }}) - floor((n - 1) * {{ p }})) * ({{ name }}_hi - {{ name }}_lo) as {{ name }}
  {%- endfor %}
  from picked
{% endmacro %}
