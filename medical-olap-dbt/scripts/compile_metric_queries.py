"""Compile representative MetricFlow queries against the semantic manifest.

Renders Redshift SQL only; nothing is executed. Use this to confirm that the
semantic layer resolves joins and filters the way the business expects.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "medical-data-service" / "backend"))

from app.metricflow.client import MetricFlowClient  # noqa: E402

MANIFEST = Path(__file__).resolve().parent.parent / "target" / "semantic_manifest.json"

CASES: list[tuple[str, list[str], list[str]]] = [
    ("按月看净销售额", ["net_sales_amount"], ["metric_time__month"]),
    ("按大区名称看净销售额", ["net_sales_amount"], ["sales_region__sales_region_name"]),
    ("按渠道名称看净销售额", ["net_sales_amount"], ["channel__channel_name"]),
    ("按医院等级看净销售额", ["net_sales_amount"], ["customer__hospital_level"]),
    ("按处方药属性看净销售额", ["net_sales_amount"], ["product__product_category"]),
    ("按销售代表看净销售额", ["net_sales_amount"], ["sales_rep__sales_rep_name"]),
    ("订单数（去重）", ["order_count"], ["metric_time__year"]),
    ("活跃客户数按治疗领域", ["active_customer_count"], ["product__therapy_area"]),
    ("回款率（跨模型比率）", ["payment_rate"], ["metric_time__year"]),
    ("营销 ROI（跨模型比率）", ["roi"], ["metric_time__year"]),
    ("平均订单金额", ["average_order_value"], ["metric_time__year"]),
    ("处方量按治疗领域", ["prescription_count"], ["product__therapy_area"]),
]


def main() -> int:
    client = MetricFlowClient(str(MANIFEST))
    failures = 0

    for title, metrics, group_by in CASES:
        print(f"### {title}")
        print(f"    metrics={metrics} group_by={group_by}")
        try:
            sql = client.explain(metrics, group_by)["sql"]
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    FAILED: {type(exc).__name__}: {exc}")
        else:
            print("\n".join(f"    {line}" for line in sql.splitlines()))
        print()

    print(f"compiled {len(CASES) - failures}/{len(CASES)} queries")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
