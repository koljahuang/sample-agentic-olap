"""Show what the DWS pre-aggregation destroys.

sales_daily_summary groups by
    stat_date, sales_region_id, customer_id, product_id, sales_rep_id, channel_id
and computes count(distinct order_id) per group. An order spanning two products
lands in two groups, each carrying order_count = 1, so SUM(order_count)
double-counts the order. The atomic grain no longer exists in the table, so the
correct number cannot be recovered from it.
"""

import collections
import csv
from datetime import date
from pathlib import Path

SEEDS = Path(__file__).resolve().parent.parent / "seeds"


def rows(name: str) -> list[dict[str, str]]:
    with (SEEDS / name).open() as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    orders = {r["order_id"]: r for r in rows("seed_sales_orders.csv")}
    items = rows("seed_sales_order_items.csv")
    customers = {r["customer_id"]: r for r in rows("seed_customers.csv")}

    completed = {k for k, v in orders.items() if v["order_status"] == "COMPLETED"}

    # Truth, computed from the atomic grain (DWD).
    true_orders = len({i["order_id"] for i in items if i["order_id"] in completed})
    true_customers = len(
        {orders[i["order_id"]]["customer_id"] for i in items if i["order_id"] in completed}
    )
    true_sales = sum(float(i["net_amount"]) for i in items if i["order_id"] in completed)

    # Rebuild the DWS grain, then aggregate it the way a semantic layer would.
    groups: dict[tuple, set[str]] = collections.defaultdict(set)
    for item in items:
        order_id = item["order_id"]
        if order_id not in completed:
            continue
        order = orders[order_id]
        customer = customers[order["customer_id"]]
        key = (
            order["order_date"],
            customer["sales_region_id"],
            order["customer_id"],
            item["product_id"],
            order["sales_rep_id"],
            order["channel_id"],
        )
        groups[key].add(order_id)

    dws_order_count = sum(len(order_ids) for order_ids in groups.values())

    print(f"DWS rows                              {len(groups):>12,}")
    print()
    print("order count")
    print(f"  truth: COUNT(DISTINCT order_id)     {true_orders:>12,}")
    print(f"  DWS:   SUM(order_count)             {dws_order_count:>12,}"
          f"   ({dws_order_count / true_orders:,.2f}x)")
    print()
    print("average order value (net sales / orders)")
    print(f"  truth                               {true_sales / true_orders:>12,.0f}")
    print(f"  DWS                                 {true_sales / dws_order_count:>12,.0f}")
    print()
    print("distinct customers (still recoverable, customer_id survives the grain)")
    print(f"  truth                               {true_customers:>12,}")


if __name__ == "__main__":
    main()
