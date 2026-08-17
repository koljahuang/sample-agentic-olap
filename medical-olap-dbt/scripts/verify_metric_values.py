"""Verify the semantic-layer metric definitions against the raw seeds.

Each metric is recomputed in Python following the definition recorded in
models/dwd/schema.yml, then compared with the pre-refactor DWS behaviour. This
is a correctness check for the definitions, not a substitute for running the
generated SQL on Redshift.
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
    touchpoints = rows("seed_campaign_touchpoints.csv")

    completed = {k for k, v in orders.items() if v["order_status"] == "COMPLETED"}
    sales_items = [i for i in items if i["order_id"] in completed]

    # --- semantic layer definitions -------------------------------------
    net_sales_amount = sum(float(i["net_amount"]) for i in sales_items)
    order_count = len({i["order_id"] for i in sales_items})
    order_item_count = len(sales_items)
    active_customer_count = len({orders[i["order_id"]]["customer_id"] for i in sales_items})
    payment_amount = sum(
        float(p["payment_amount"]) for p in payments if p["order_id"] in completed
    )
    campaign_cost = sum(float(t["cost_amount"]) for t in touchpoints)
    payment_rate = payment_amount / net_sales_amount
    average_order_value = net_sales_amount / order_count

    # --- what the old DWS-based definitions produced --------------------
    items_per_order = collections.Counter(i["order_id"] for i in items)
    dws_groups: set[tuple] = set()
    dws_order_count = 0
    for item in sales_items:
        order = orders[item["order_id"]]
        key = (order["order_date"], order["customer_id"], item["product_id"])
        if key not in dws_groups:
            dws_groups.add(key)
        dws_order_count += 1  # one order_count=1 per (order, product) row
    dws_payment_amount = sum(
        float(p["payment_amount"]) * items_per_order[p["order_id"]]
        for p in payments
        if p["order_id"] in completed
    )
    dws_payment_rate = dws_payment_amount / net_sales_amount

    # touchpoint fan-out that used to inflate campaign_cost
    touch_by_customer: dict[str, list[str]] = collections.defaultdict(list)
    for touch in touchpoints:
        touch_by_customer[touch["customer_id"]].append(touch["touchpoint_time"])
    bridge_cost = 0.0
    for item in sales_items:
        order = orders[item["order_id"]]
        for touch in touchpoints:
            if touch["customer_id"] != order["customer_id"]:
                continue
            if touch["touchpoint_time"] <= order["order_date"]:
                bridge_cost += float(touch["cost_amount"])

    print("metric                     semantic layer            previous (DWS/ADS)")
    print("-" * 78)
    print(f"net_sales_amount      {net_sales_amount:>20,.0f}      {net_sales_amount:>20,.0f}")
    print(f"order_count           {order_count:>20,}      {dws_order_count:>20,}"
          f"  ({dws_order_count / order_count:.2f}x)")
    print(f"order_item_count      {order_item_count:>20,}      {'n/a':>20}")
    print(f"active_customer_count {active_customer_count:>20,}      {'n/a':>20}")
    print(f"payment_amount        {payment_amount:>20,.0f}      {dws_payment_amount:>20,.0f}"
          f"  ({dws_payment_amount / payment_amount:.2f}x)")
    print(f"campaign_cost         {campaign_cost:>20,.0f}      {bridge_cost:>20,.0f}"
          f"  ({bridge_cost / campaign_cost:.1f}x)")
    print(f"payment_rate          {payment_rate:>19.2%}      {dws_payment_rate:>19.2%}")
    print(f"average_order_value   {average_order_value:>20,.0f}      "
          f"{net_sales_amount / dws_order_count:>20,.0f}")


if __name__ == "__main__":
    main()
