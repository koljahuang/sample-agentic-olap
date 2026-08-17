#!/usr/bin/env python3
"""Generate a story-driven synthetic dataset for the medical sales warehouse.

The data is deterministic (fixed RNG) and spans 2024-2026 so that these questions
have clear answers:

  1. Regional trend 2024 -> 2025 -> 2026
     East steady up, South strong up, North flat, West emerging (low base, high growth).
  2. Active-customer growth concentrated in which therapy area?
     Oncology (肿瘤) and Immunology (免疫) grow fastest.
  3. Sales-share shift attributed to category?
     Oncology + Immunology share rises; Respiratory + Cardiovascular share falls.

Run:
    python medical-olap-dbt/scripts/generate_seeds.py
It overwrites the CSVs in medical-olap-dbt/seeds/.
"""
from __future__ import annotations

import csv
import os
import random
from datetime import date, timedelta

RNG = random.Random(20260814)
SEEDS_DIR = os.path.join(os.path.dirname(__file__), "..", "seeds")
YEARS = [2024, 2025, 2026]

# --- Regions: base scale + yearly growth multiplier ---
REGIONS = {
    "R_EAST":  {"name": "华东", "base": 1.00, "growth": 1.10, "provinces": [("310000", "310100"), ("320000", "320100")]},
    "R_SOUTH": {"name": "华南", "base": 0.70, "growth": 1.32, "provinces": [("440000", "440100"), ("350000", "350100")]},
    "R_NORTH": {"name": "华北", "base": 0.70, "growth": 0.90, "provinces": [("110000", "110100"), ("120000", "120100")]},
    "R_WEST":  {"name": "西部", "base": 0.30, "growth": 1.55, "provinces": [("510000", "510100"), ("610000", "610100")]},
}

# --- Therapy areas (categories): base share + yearly growth multiplier ---
AREAS = {
    "肿瘤":   {"growth": 1.38, "share": 0.22, "price": (800, 2000)},
    "免疫":   {"growth": 1.50, "share": 0.12, "price": (900, 2400)},
    "糖尿病": {"growth": 1.10, "share": 0.28, "price": (120, 300)},
    "心血管": {"growth": 1.00, "share": 0.24, "price": (100, 260)},
    "呼吸":   {"growth": 0.85, "share": 0.14, "price": (80, 220)},
}

CUSTOMER_TYPES = ["医院", "药店", "诊所"]
HOSPITAL_LEVELS = {"医院": ["三甲", "三乙", "二甲"], "药店": [""], "诊所": ["一级"]}
CHANNEL = {"医院": "HOSPITAL", "药店": "PHARMACY", "诊所": "CLINIC"}
TOUCHPOINT_TYPES = ["CONFERENCE", "HOSPITAL_VISIT", "ONLINE", "EMAIL"]

BASE_MONTHLY_ORDERS = 26  # global scale knob


def seasonal(month: int) -> float:
    # mild seasonality: Q4 stronger, Feb weaker
    return {1: 0.9, 2: 0.75, 3: 1.0, 4: 1.05, 5: 1.0, 6: 1.05,
            7: 0.95, 8: 0.95, 9: 1.1, 10: 1.1, 11: 1.15, 12: 1.2}[month]


def write_csv(name: str, header: list[str], rows: list[list]):
    path = os.path.join(SEEDS_DIR, name)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"{name:34s} {len(rows):>6d} rows")


def build_customers():
    rows = []
    idx = 1
    # ~30 customers per region
    per_region = 30
    region_customers: dict[str, list[str]] = {r: [] for r in REGIONS}
    for region, meta in REGIONS.items():
        for _ in range(per_region):
            cid = f"C{idx:04d}"
            ctype = RNG.choices(CUSTOMER_TYPES, weights=[0.55, 0.30, 0.15])[0]
            level = RNG.choice(HOSPITAL_LEVELS[ctype])
            prov, city = RNG.choice(meta["provinces"])
            segment = RNG.choices(["A", "B", "C"], weights=[0.25, 0.45, 0.30])[0]
            name = f"{meta['name']}{ctype}{idx:03d}"
            rows.append([cid, name, ctype, level, prov, city, region, segment])
            region_customers[region].append(cid)
            idx += 1
    write_csv("seed_customers.csv",
              ["customer_id", "customer_name", "customer_type", "hospital_level",
               "province_code", "city_code", "sales_region_id", "customer_segment"], rows)
    return region_customers


def build_products():
    rows = []
    area_products: dict[str, list[tuple[str, float]]] = {a: [] for a in AREAS}
    idx = 1
    for area, meta in AREAS.items():
        for n in range(6):  # 6 products per area -> 30 total
            pid = f"P{idx:03d}"
            lo, hi = meta["price"]
            price = round(RNG.uniform(lo, hi), 2)
            name = f"{area}药{chr(65 + n)}"
            generic = f"通用名{idx}"
            category = "处方药" if RNG.random() < 0.8 else "OTC"
            rows.append([pid, name, generic, area, category, price])
            area_products[area].append((pid, price))
            idx += 1
    write_csv("seed_products.csv",
              ["product_id", "product_name", "generic_name", "therapy_area",
               "product_category", "list_price"], rows)
    return area_products


def build_reps():
    rows = []
    region_reps: dict[str, list[str]] = {r: [] for r in REGIONS}
    idx = 1
    for region, meta in REGIONS.items():
        for n in range(6):  # 6 reps per region
            rid = f"SR{idx:03d}"
            rows.append([rid, f"{meta['name']}代表{n + 1}", region])
            region_reps[region].append(rid)
            idx += 1
    write_csv("seed_sales_reps.csv",
              ["sales_rep_id", "sales_rep_name", "sales_region_id"], rows)
    return region_reps


def active_pool(region_customers, region, area, year_offset):
    """Distinct active customers grows with the area growth (story Q2)."""
    pool = region_customers[region]
    base_frac = {"肿瘤": 0.30, "免疫": 0.18, "糖尿病": 0.55, "心血管": 0.50, "呼吸": 0.35}[area]
    frac = min(1.0, base_frac * (AREAS[area]["growth"] ** year_offset))
    size = max(1, round(len(pool) * frac))
    # deterministic subset seeded by region/area/year
    r = random.Random(hash((region, area, year_offset)) & 0xFFFFFFFF)
    return r.sample(pool, size)


def main():
    region_customers = build_customers()
    area_products = build_products()
    region_reps = build_reps()

    orders, items, payments, touchpoints, prescriptions = [], [], [], [], []
    oid = pid_o = pay = tp = rx = 1
    campaigns = [f"CAMP{n:03d}" for n in range(1, 16)]

    for year in YEARS:
        yo = year - 2024
        for month in range(1, 13):
            for region, rmeta in REGIONS.items():
                for area, ameta in AREAS.items():
                    expected = (BASE_MONTHLY_ORDERS * rmeta["base"] * (rmeta["growth"] ** yo)
                                * ameta["share"] * (ameta["growth"] ** yo) * seasonal(month))
                    n_orders = max(0, int(RNG.gauss(expected, expected * 0.15)))
                    if n_orders == 0:
                        continue
                    pool = active_pool(region_customers, region, area, yo)
                    reps = region_reps[region]
                    prods = area_products[area]
                    for _ in range(n_orders):
                        cust = RNG.choice(pool)
                        rep = RNG.choice(reps)
                        day = RNG.randint(1, 28)
                        odate = date(year, month, day)
                        status = "COMPLETED" if RNG.random() < 0.93 else "CANCELLED"
                        ctype_channel = RNG.choice(["HOSPITAL", "PHARMACY", "CLINIC"])
                        order_id = f"O{oid:06d}"
                        orders.append([order_id, cust, odate.isoformat(), status, rep, ctype_channel])

                        order_net = 0.0
                        for _ in range(RNG.randint(1, 3)):
                            prod_id, list_price = RNG.choice(prods)
                            qty = RNG.randint(5, 120)
                            unit = round(list_price * RNG.uniform(0.9, 1.05), 2)
                            gross = qty * unit
                            discount = round(gross * RNG.uniform(0, 0.12), 2)
                            net = round(gross - discount, 2)
                            order_net += net
                            items.append([f"OI{pid_o:07d}", order_id, prod_id, qty, unit, discount, net])
                            pid_o += 1

                        if status == "COMPLETED":
                            pstatus = RNG.choices(["PAID", "PARTIAL"], weights=[0.8, 0.2])[0]
                            pamount = round(order_net if pstatus == "PAID" else order_net * RNG.uniform(0.3, 0.7), 2)
                            pdate = odate + timedelta(days=RNG.randint(10, 40))
                            payments.append([f"PAY{pay:06d}", order_id, pdate.isoformat(), pamount, pstatus])
                            pay += 1

                        # marketing touchpoints before the order (drives attribution)
                        for _ in range(RNG.randint(0, 2)):
                            t_before = odate - timedelta(days=RNG.randint(3, 40))
                            if t_before.year < 2024:
                                continue
                            cost = round(RNG.uniform(200, 3000), 2)
                            touchpoints.append([f"T{tp:06d}", cust, RNG.choice(campaigns),
                                                t_before.isoformat(), RNG.choice(TOUCHPOINT_TYPES), rep, cost])
                            tp += 1

                        # prescription events tied to the same customer + one product in area
                        if RNG.random() < 0.6:
                            prod_id, _ = RNG.choice(prods)
                            pc = RNG.randint(5, 120)
                            prescriptions.append([f"RX{rx:06d}", cust, prod_id, odate.isoformat(),
                                                  pc, max(1, int(pc * RNG.uniform(0.6, 0.95)))])
                            rx += 1
                        oid += 1

    write_csv("seed_sales_orders.csv",
              ["order_id", "customer_id", "order_date", "order_status", "sales_rep_id", "channel_id"], orders)
    write_csv("seed_sales_order_items.csv",
              ["order_item_id", "order_id", "product_id", "quantity", "unit_price", "discount_amount", "net_amount"], items)
    write_csv("seed_payments.csv",
              ["payment_id", "order_id", "payment_date", "payment_amount", "payment_status"], payments)
    write_csv("seed_campaign_touchpoints.csv",
              ["touchpoint_id", "customer_id", "campaign_id", "touchpoint_time", "touchpoint_type", "sales_rep_id", "cost_amount"], touchpoints)
    write_csv("seed_prescriptions.csv",
              ["prescription_event_id", "customer_id", "product_id", "event_date", "prescription_count", "patient_count"], prescriptions)

    # date spine 2024-01-01 .. 2026-12-31
    spine = []
    d = date(2024, 1, 1)
    end = date(2026, 12, 31)
    while d <= end:
        spine.append([d.isoformat()])
        d += timedelta(days=1)
    write_csv("seed_date_spine.csv", ["date_day"], spine)


if __name__ == "__main__":
    main()
