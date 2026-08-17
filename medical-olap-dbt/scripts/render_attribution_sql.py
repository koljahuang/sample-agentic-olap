"""Render fct_campaign_attribution.sql without a database connection.

`dbt compile` needs a live warehouse, which makes it awkward to check that the
lookback-window Jinja produces the SQL you expect. This renders the template
with stubbed `ref` and `var` so the join predicate can be inspected directly.
"""

import argparse
from pathlib import Path

from jinja2 import Environment

MODEL = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "dwd"
    / "fct_campaign_attribution.sql"
)


def render(lookback_days: int) -> str:
    environment = Environment()
    template = environment.from_string(MODEL.read_text())
    return template.render(
        ref=lambda name: f'"medical_dw"."dwd"."{name}"',
        var=lambda name, default=None: (
            lookback_days if name == "attribution_lookback_days" else default
        ),
        config=lambda **_: "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--join-only", action="store_true",
                        help="Print just the touchpoint join predicate.")
    args = parser.parse_args()

    sql = render(args.lookback_days)
    if not args.join_only:
        print(sql)
        return

    lines = sql.splitlines()
    start = next(i for i, line in enumerate(lines) if "from sales s" in line)
    end = next(i for i, line in enumerate(lines[start:], start) if line.startswith(")"))
    print("\n".join(lines[start:end]))


if __name__ == "__main__":
    main()
