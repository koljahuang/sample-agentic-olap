select
    product_id,
    product_name,
    generic_name,
    therapy_area,
    product_category,
    cast(list_price as decimal(18,2)) as list_price,
    current_timestamp as etl_loaded_at
from {{ ref('seed_products') }}
