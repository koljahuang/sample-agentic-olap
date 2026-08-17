select
    order_id,
    customer_id,
    cast(order_date as date) as order_date,
    order_status,
    sales_rep_id,
    channel_id,
    current_timestamp as etl_loaded_at
from {{ ref('seed_sales_orders') }}
