-- Conformed sales region dimension.
-- Region codes appear on several facts; this table gives them a display name so
-- the semantic layer can group by a readable region instead of a raw code.
with mapping as (
    select 'R_EAST'  as sales_region_id, '华东' as sales_region_name
    union all select 'R_SOUTH', '华南'
    union all select 'R_NORTH', '华北'
    union all select 'R_WEST',  '西部'
)
select
    sales_region_id,
    sales_region_name,
    current_timestamp as etl_loaded_at
from mapping
