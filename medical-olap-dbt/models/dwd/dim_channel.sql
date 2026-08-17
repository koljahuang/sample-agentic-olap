-- Conformed sales channel dimension.
with mapping as (
    select 'HOSPITAL' as channel_id, '医院渠道' as channel_name
    union all select 'PHARMACY', '药店渠道'
    union all select 'CLINIC',   '诊所渠道'
)
select
    channel_id,
    channel_name,
    current_timestamp as etl_loaded_at
from mapping
