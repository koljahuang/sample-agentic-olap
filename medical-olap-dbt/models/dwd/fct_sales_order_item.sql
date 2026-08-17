-- Atomic sales fact at order-item grain.
-- Carries the full foreign-key set so the semantic layer never needs a
-- multi-hop join to reach a conformed dimension.
select
    oi.order_item_id,
    oi.order_id,
    o.order_date,
    o.order_status,
    o.customer_id,
    oi.product_id,
    o.sales_rep_id,
    o.channel_id,
    c.sales_region_id,
    cast(oi.quantity as integer) as quantity,
    cast(oi.unit_price as decimal(18,2)) as unit_price,
    cast(oi.discount_amount as decimal(18,2)) as discount_amount,
    cast(oi.net_amount as decimal(18,2)) as net_amount,
    current_timestamp as etl_loaded_at
from {{ ref('seed_sales_order_items') }} oi
join {{ ref('fct_sales_order') }} o
  on o.order_id = oi.order_id
join {{ ref('dim_customer') }} c
  on c.customer_id = o.customer_id
