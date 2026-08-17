select
    customer_id,
    customer_name,
    customer_type,
    hospital_level,
    province_code,
    city_code,
    sales_region_id,
    customer_segment,
    true as is_current,
    current_timestamp as etl_loaded_at
from {{ ref('seed_customers') }}
