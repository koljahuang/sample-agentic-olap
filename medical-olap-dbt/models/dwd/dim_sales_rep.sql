select
    sales_rep_id,
    sales_rep_name,
    sales_region_id,
    current_timestamp as etl_loaded_at
from {{ ref('seed_sales_reps') }}
