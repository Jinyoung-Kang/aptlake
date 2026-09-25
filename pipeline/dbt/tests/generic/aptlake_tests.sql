{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

{% test expression_is_true(model, expression) %}
select * from {{ model }} where not coalesce(({{ expression }}), false)
{% endtest %}

{% test value_between(model, column_name, min_value, max_value) %}
select {{ column_name }} from {{ model }}
where {{ column_name }} is not null
  and not ({{ column_name }} > {{ min_value }} and {{ column_name }} < {{ max_value }})
{% endtest %}
