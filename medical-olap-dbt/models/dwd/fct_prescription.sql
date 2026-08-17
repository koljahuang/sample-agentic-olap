-- Prescription event fact at event grain.
select
    rx.prescription_event_id,
    rx.customer_id,
    rx.product_id,
    c.sales_region_id,
    cast(rx.event_date as date) as event_date,
    cast(rx.prescription_count as integer) as prescription_count,
    cast(rx.patient_count as integer) as patient_count,
    current_timestamp as etl_loaded_at
from {{ ref('seed_prescriptions') }} rx
join {{ ref('dim_customer') }} c
  on c.customer_id = rx.customer_id
