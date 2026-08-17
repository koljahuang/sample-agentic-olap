-- Detail-serving wide table for BI drill-down and for RLS / DDM policy demos.
--
-- Grain: order item x marketing touchpoint, inherited from the attribution
-- bridge. Only attributed_revenue is pre-allocated and therefore safe to sum.
-- net_sales_amount, payment_amount and campaign_cost repeat across touchpoints
-- by construction, so they are here for row-level inspection only. Aggregate
-- those through the semantic layer, which reads them at their native grain.
with payments as (
    select
        order_id,
        sum(payment_amount) as payment_amount
    from {{ ref('fct_payment') }}
    where order_status = 'COMPLETED'
    group by 1
)
select
    a.order_id,
    a.order_item_id,
    a.order_date,
    a.customer_id,
    c.customer_name,
    c.customer_type,
    c.hospital_level,
    c.province_code,
    c.city_code,
    c.sales_region_id,
    rg.sales_region_name,
    a.product_id,
    p.product_name,
    p.generic_name,
    p.therapy_area,
    p.product_category,
    oi.sales_rep_id,
    r.sales_rep_name,
    oi.channel_id,
    ch.channel_name,
    a.campaign_id,
    a.touchpoint_id,
    a.touchpoint_type,
    a.touchpoint_time,
    a.attribution_model,
    a.attribution_weight,
    a.attributed_revenue,
    a.cost_amount as campaign_cost,
    oi.quantity,
    oi.unit_price,
    oi.discount_amount,
    oi.net_amount as net_sales_amount,
    coalesce(pay.payment_amount, 0) as payment_amount,
    c.sales_region_id as data_region_code,
    oi.sales_rep_id as data_owner_id,
    'L2' as sensitive_level
from {{ ref('fct_campaign_attribution') }} a
join {{ ref('fct_sales_order_item') }} oi
  on oi.order_item_id = a.order_item_id
join {{ ref('dim_customer') }} c
  on c.customer_id = a.customer_id
join {{ ref('dim_product') }} p
  on p.product_id = a.product_id
join {{ ref('dim_sales_rep') }} r
  on r.sales_rep_id = oi.sales_rep_id
join {{ ref('dim_sales_region') }} rg
  on rg.sales_region_id = c.sales_region_id
join {{ ref('dim_channel') }} ch
  on ch.channel_id = oi.channel_id
left join payments pay
  on pay.order_id = a.order_id
