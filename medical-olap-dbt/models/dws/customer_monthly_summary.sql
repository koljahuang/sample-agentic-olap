-- Derived cache: monthly customer activity.
--
-- order_count is safe at this grain because an order belongs to exactly one
-- customer and falls in exactly one month, so distinct counts do not overlap
-- across rows.
with sales as (
    select
        date_trunc('month', order_date)::date as stat_month,
        customer_id,
        count(distinct order_id) as order_count,
        sum(net_amount) as sales_amount
    from {{ ref('fct_sales_order_item') }}
    where order_status = 'COMPLETED'
    group by 1, 2
), touches as (
    select
        date_trunc('month', touchpoint_time)::date as stat_month,
        customer_id,
        count(*) as touchpoint_count
    from {{ ref('fct_campaign_touchpoint') }}
    group by 1, 2
)
select
    s.stat_month,
    s.customer_id,
    coalesce(t.touchpoint_count, 0) as touchpoint_count,
    s.order_count,
    s.sales_amount
from sales s
left join touches t
  on t.stat_month = s.stat_month
 and t.customer_id = s.customer_id
