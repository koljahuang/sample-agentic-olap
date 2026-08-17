-- Payment fact at payment grain (one row per payment record).
-- Order attributes are denormalized onto the fact so payment metrics can be
-- grouped by customer, rep, channel and region without a multi-hop join.
-- Product is intentionally absent: a payment settles an order, not a line item.
select
    p.payment_id,
    p.order_id,
    cast(p.payment_date as date) as payment_date,
    cast(p.payment_amount as decimal(18,2)) as payment_amount,
    p.payment_status,
    o.order_date,
    o.order_status,
    o.customer_id,
    o.sales_rep_id,
    o.channel_id,
    c.sales_region_id,
    current_timestamp as etl_loaded_at
from {{ ref('seed_payments') }} p
join {{ ref('fct_sales_order') }} o
  on o.order_id = p.order_id
join {{ ref('dim_customer') }} c
  on c.customer_id = o.customer_id
