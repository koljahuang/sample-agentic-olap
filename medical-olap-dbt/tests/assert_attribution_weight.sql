select
    order_item_id,
    attribution_model,
    sum(attribution_weight) as weight_sum
from {{ ref('fct_campaign_attribution') }}
group by 1, 2
having abs(weight_sum - 1) > 0.0001
