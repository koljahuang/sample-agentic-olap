-- Derived cache over the atomic sales fact.
--
-- Additive measures only. order_count is excluded because one order spans
-- several products: SUM(order_count) over this grain would double count.
-- payment_amount is excluded because payments settle orders, not order items.
-- Both are available from the semantic layer at their correct grain.
select
    order_date as stat_date,
    sales_region_id,
    customer_id,
    product_id,
    sales_rep_id,
    channel_id,
    sum(quantity) as quantity,
    sum(net_amount) as net_sales_amount
from {{ ref('fct_sales_order_item') }}
where order_status = 'COMPLETED'
group by 1, 2, 3, 4, 5, 6
