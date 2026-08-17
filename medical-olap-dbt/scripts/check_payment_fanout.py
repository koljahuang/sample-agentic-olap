"""Check whether payment_amount inflates in the DWS and ADS models.

fct_payment is at order grain. Both sales_daily_summary and
ads_sales_attribution_wide join it onto an order-item grain row set, so the
order-level payment repeats once per order item.
"""

import collections
import csv
from pathlib import Path

SEEDS = Path(__file__).resolve().parent.parent / "seeds"


def rows(name: str) -> list[dict[str, str]]:
    with (SEEDS / name).open() as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    orders = {r["order_id"]: r for r in rows("seed_sales_orders.csv")}
    items = rows("seed_sales_order_items.csv")
    payments = rows("seed_payments.csv")

    payment_by_order: dict[str, float] = collections.defaultdict(float)
    for payment in payments:
        if payment["payment_status"] in ("PAID", "PARTIAL"):
            payment_by_order[payment["order_id"]] += float(payment["payment_amount"])

    items_per_order: dict[str, int] = collections.Counter(i["order_id"] for i in items)

    completed = {k for k, v in orders.items() if v["order_status"] == "COMPLETED"}

    true_payment = sum(
        amount for order_id, amount in payment_by_order.items() if order_id in completed
    )

    dws_payment = 0.0
    for order_id in completed:
        amount = payment_by_order.get(order_id, 0.0)
        dws_payment += amount * items_per_order.get(order_id, 0)

    true_sales = sum(
        float(i["net_amount"]) for i in items if i["order_id"] in completed
    )

    print(f"true payment (order grain)          {true_payment:>16,.0f}")
    print(f"DWS payment (order-item grain)      {dws_payment:>16,.0f}"
          f"   ({dws_payment / true_payment:,.2f}x)")
    print()
    print(f"true net sales                      {true_sales:>16,.0f}")
    print(f"true payment rate                   {true_payment / true_sales:>16,.2%}")
    print(f"DWS payment rate                    {dws_payment / true_sales:>16,.2%}")


if __name__ == "__main__":
    main()
