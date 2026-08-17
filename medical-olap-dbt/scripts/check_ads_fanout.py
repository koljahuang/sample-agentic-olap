"""Explain the fan-out in the ADS wide table using the raw seeds.

The ADS wide table is at order-item x touchpoint grain. Order-item level
amounts repeat once per matched touchpoint, so summing them directly inflates
the result. The pre-allocated ``attributed_revenue`` column does not inflate.

This script shows both, plus how an attribution lookback window changes the
fan-out factor.
"""

import collections
import csv
from datetime import date
from pathlib import Path

SEEDS = Path(__file__).resolve().parent.parent / "seeds"


def rows(name: str) -> list[dict[str, str]]:
    with (SEEDS / name).open() as handle:
        return list(csv.DictReader(handle))


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def main() -> None:
    orders = {r["order_id"]: r for r in rows("seed_sales_orders.csv")}
    items = rows("seed_sales_order_items.csv")
    touchpoints = rows("seed_campaign_touchpoints.csv")

    touch_dates: dict[str, list[date]] = collections.defaultdict(list)
    for touch in touchpoints:
        touch_dates[touch["customer_id"]].append(parse_date(touch["touchpoint_time"]))
    for values in touch_dates.values():
        values.sort()

    print(f"customers                  {len(set(touch_dates)):>14,}")
    print(f"touchpoints                {len(touchpoints):>14,}")
    print(f"touchpoints per customer   {len(touchpoints) / len(touch_dates):>14,.1f}")
    print()

    windows = [None, 90, 30]
    for window_days in windows:
        true_sales = 0.0
        repeated_sales = 0.0
        allocated_revenue = 0.0
        matched_rows = 0
        item_rows = 0
        match_counts: list[int] = []

        for item in items:
            order = orders[item["order_id"]]
            if order["order_status"] != "COMPLETED":
                continue

            item_rows += 1
            net_amount = float(item["net_amount"])
            true_sales += net_amount

            order_date = parse_date(order["order_date"])
            matched = 0
            for touch_date in touch_dates.get(order["customer_id"], ()):
                if touch_date > order_date:
                    break
                if window_days is not None and (order_date - touch_date).days > window_days:
                    continue
                matched += 1

            if not matched:
                continue

            match_counts.append(matched)
            matched_rows += matched
            repeated_sales += net_amount * matched
            allocated_revenue += (net_amount / matched) * matched

        label = "no window" if window_days is None else f"{window_days}-day window"
        print(f"--- attribution match rule: {label}")
        print(f"  order items matched         {len(match_counts):>14,} / {item_rows:,}")
        print(f"  ADS rows                    {matched_rows:>14,}")
        print(f"  avg touchpoints per item    {matched_rows / max(len(match_counts), 1):>14,.1f}")
        print(f"  true net sales              {true_sales:>14,.0f}")
        print(f"  SUM(net_sales_amount)       {repeated_sales:>14,.0f}"
              f"   ({repeated_sales / true_sales:,.1f}x)")
        print(f"  SUM(attributed_revenue)     {allocated_revenue:>14,.0f}"
              f"   ({allocated_revenue / true_sales:,.2f}x)")
        print()


if __name__ == "__main__":
    main()
