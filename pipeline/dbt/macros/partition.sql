{# 월 파티션 필터. var 는 Dagster 가 넘기지만, SQL 에 직접 들어가므로 형식을 강제 검증한다 (주입 방지). #}
{% macro deal_ym_var() %}
  {%- set v = var('deal_ym', '') | string -%}
  {%- if v == '' -%}{{ return('') }}{%- endif -%}
  {%- if not modules.re.fullmatch('[0-9]{6}', v) -%}
    {{ exceptions.raise_compiler_error("deal_ym var must be YYYYMM, got: " ~ v) }}
  {%- endif -%}
  {{ return(v) }}
{% endmacro %}

{% macro deal_ym_filter(column='deal_ym') %}
  {%- set v = deal_ym_var() -%}
  {%- if v != '' -%} {{ column }} = '{{ v }}' {%- else -%} true {%- endif -%}
{% endmacro %}

{# 스키마 이름에 target 접두사를 붙이지 않는다 (lake.gold.*) #}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ custom_schema_name if custom_schema_name else target.schema }}
{%- endmacro %}
