-- Marketing touchpoint fact at touchpoint grain.
-- cost_amount lives here at its native grain, so summing it is always correct.
select
    t.touchpoint_id,
    t.customer_id,
    t.campaign_id,
    cast(t.touchpoint_time as timestamp) as touchpoint_time,
    t.touchpoint_type,
    t.sales_rep_id,
    c.sales_region_id,
    cast(t.cost_amount as decimal(18,2)) as cost_amount,
    current_timestamp as etl_loaded_at
from {{ ref('seed_campaign_touchpoints') }} t
join {{ ref('dim_customer') }} c
  on c.customer_id = t.customer_id
