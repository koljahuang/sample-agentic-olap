-- Attribution bridge fact at (order item x touchpoint) grain.
--
-- This is an atomic bridge, not a pre-aggregate: one row per order-item and
-- marketing-touchpoint pair. Revenue is split linearly across the matched
-- touchpoints, so summing attributed_revenue is conservative.
--
-- cost_amount is carried for detail queries only. Do not aggregate it here:
-- one touchpoint can match many order items, so the cost repeats. Campaign cost
-- metrics belong to fct_campaign_touchpoint, which holds it at its native grain.
--
-- Matching is bounded by a lookback window (var: attribution_lookback_days).
-- Order items with no touchpoint inside the window are absent from this table:
-- that is organic demand, and crediting it to marketing would be wrong.
{% set lookback_days = var('attribution_lookback_days', 90) | int %}
with sales as (
    select
        order_id,
        order_item_id,
        order_date,
        customer_id,
        product_id,
        sales_region_id,
        net_amount
    from {{ ref('fct_sales_order_item') }}
    where order_status = 'COMPLETED'
), matched as (
    select
        s.order_id,
        s.order_item_id,
        s.order_date,
        s.customer_id,
        s.product_id,
        s.sales_region_id,
        s.net_amount,
        t.touchpoint_id,
        t.campaign_id,
        t.touchpoint_time,
        t.touchpoint_type,
        t.sales_rep_id as touchpoint_sales_rep_id,
        t.cost_amount,
        count(*) over (partition by s.order_item_id) as touchpoint_count
    from sales s
    join {{ ref('fct_campaign_touchpoint') }} t
      on t.customer_id = s.customer_id
     and t.touchpoint_time <= s.order_date
     {%- if lookback_days > 0 %}
     and t.touchpoint_time >= dateadd(day, -{{ lookback_days }}, s.order_date)
     {%- endif %}
)
select
    order_id,
    order_item_id,
    order_date,
    customer_id,
    product_id,
    sales_region_id,
    touchpoint_id,
    campaign_id,
    touchpoint_time,
    touchpoint_type,
    touchpoint_sales_rep_id,
    cost_amount,
    'linear' as attribution_model,
    cast(1.0 / nullif(touchpoint_count, 0) as decimal(18,8)) as attribution_weight,
    cast(net_amount / nullif(touchpoint_count, 0) as decimal(18,2)) as attributed_revenue,
    current_timestamp as etl_loaded_at
from matched
