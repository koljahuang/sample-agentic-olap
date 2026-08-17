"""Measure what the attribution lookback window changes.

Reads the configured window from dbt_project.yml, then reports coverage and
fan-out for that window alongside the alternatives, so the choice is a decision
rather than an accident.
"""

import collections
import csv
import re
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEEDS = PROJECT_ROOT / "seeds"
DBT_PROJECT = PROJECT_ROOT / "dbt_project.yml"


def rows(name: str) -> list[dict[str, str]]:
    with (SEEDS / name).open() as handle:
        return list(csv.DictReader(handle))


def configured_window() -> int:
    match = re.search(r"attribution_lookback_days:\s*(\d+)", DBT_PROJECT.read_text())
    return int(match.group(1)) if match else 0


def main() -> None:
    configured = configured_window()
    print(f"dbt_project.yml: attribution_lookback_days = {configured}\n")

    orders = {r["order_id"]: r for r in rows("seed_sales_orders.csv")}
    items = rows("seed_sales_order_items.csv")
    touchpoints = rows("seed_campaign_touchpoints.csv")

    touch_dates: dict[str, list[date]] = collections.defaultdict(list)
    for touch in touchpoints:
        touch_dates[touch["customer_id"]].append(date.fromisoformat(touch["touchpoint_time"][:10]))
    for values in touch_dates.values():
        values.sort()

    sales_items = [
        (date.fromisoformat(orders[i["order_id"]]["order_date"]),
         orders[i["order_id"]]["customer_id"],
         float(i["net_amount"]))
        for i in items
        if orders[i["order_id"]]["order_status"] == "COMPLETED"
    ]
    true_sales = sum(amount for _, _, amount in sales_items)

    windows = sorted({0, 30, 60, 90, 180, configured})
    header = f"{'window':>10} {'matched items':>15} {'bridge rows':>13} {'avg tp/item':>12} {'attributed':>16} {'coverage':>10}"
    print(header)
    print("-" * len(header))

    for window in windows:
        matched_items = 0
        bridge_rows = 0
        attributed = 0.0
        for order_date, customer_id, amount in sales_items:
            hits = 0
            for touch_date in touch_dates.get(customer_id, ()):
                if touch_date > order_date:
                    break
                if window and (order_date - touch_date).days > window:
                    continue
                hits += 1
            if hits:
                matched_items += 1
                bridge_rows += hits
                attributed += amount
        label = "none" if window == 0 else f"{window}d"
        marker = "  <- configured" if window == configured else ""
        print(
            f"{label:>10} {matched_items:>15,} {bridge_rows:>13,} "
            f"{bridge_rows / max(matched_items, 1):>12.1f} "
            f"{attributed:>16,.0f} {attributed / true_sales:>9.1%}{marker}"
        )

    print(f"\ntotal completed order items {len(sales_items):>10,}")
    print(f"total net sales             {true_sales:>10,.0f}")
    print(
        "\nUnmatched order items are organic demand. Attributed revenue is expected "
        "to fall short of total net sales; that gap is the honest answer, not a bug."
    )


if __name__ == "__main__":
    main()
