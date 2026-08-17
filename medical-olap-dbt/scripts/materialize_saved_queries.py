"""Materialize saved queries into Redshift tables.

dbt's own `export` runner is a dbt platform feature: dbt-core has no `export` or
`sl` command, so the export blocks in models/semantic/saved_queries.yml describe
intent that nothing local acts on. This script closes that gap.

It compiles each saved query through MetricFlow, wraps the generated SQL in
CREATE TABLE AS, and runs it. Because the SQL comes from the semantic layer, an
accelerated table can never drift from the metric definition. That is the whole
point: the cache is derived from the definition, not a second copy of it.

    python scripts/materialize_saved_queries.py --list
    python scripts/materialize_saved_queries.py --dry-run
    python scripts/materialize_saved_queries.py --execute
    python scripts/materialize_saved_queries.py --execute --select campaign_roi_monthly

Execution needs Redshift credentials, the same ones dbt uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "medical-data-service" / "backend"))

DEFAULT_MANIFEST = PROJECT_ROOT / "target" / "semantic_manifest.json"


@dataclass(frozen=True)
class ExportTarget:
    saved_query: str
    schema_name: str
    table_name: str
    export_as: str
    metrics: list[str]
    group_by: list[str]

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"


def read_export_targets(manifest_path: Path) -> list[ExportTarget]:
    manifest = json.loads(manifest_path.read_text())
    targets: list[ExportTarget] = []
    for saved_query in manifest.get("saved_queries", []):
        params = saved_query["query_params"]
        for export in saved_query.get("exports", []):
            config = export.get("config", {})
            targets.append(
                ExportTarget(
                    saved_query=saved_query["name"],
                    schema_name=config.get("schema_name") or "dws",
                    table_name=config.get("alias") or export["name"],
                    export_as=(config.get("export_as") or "table").lower(),
                    metrics=list(params.get("metrics", [])),
                    group_by=list(params.get("group_by", [])),
                )
            )
    return targets


def compile_sql(manifest_path: Path, target: ExportTarget) -> str:
    """Render the saved query as Redshift SQL via MetricFlow."""
    from app.metricflow.client import MetricFlowClient

    client = MetricFlowClient(str(manifest_path))
    return client.explain_saved_query(target.saved_query)["sql"]


def build_statements(target: ExportTarget, select_sql: str) -> list[str]:
    """Return the statements to run, one per call.

    The Redshift Data API executes a single statement per request, so the drop
    and the create are kept separate rather than joined with a semicolon.
    """
    if target.export_as not in {"table", "view"}:
        raise ValueError(
            f"{target.saved_query}: unsupported export_as {target.export_as!r}. "
            "Only table and view can be materialized locally."
        )
    keyword = "TABLE" if target.export_as == "table" else "VIEW"
    body = select_sql.rstrip().rstrip(";")
    return [
        f"DROP {keyword} IF EXISTS {target.qualified_name};",
        f"CREATE {keyword} {target.qualified_name} AS\n{body};",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--select", action="append", default=[],
                        help="Saved query name; repeatable. Default is all.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--list", action="store_true", help="List export targets only.")
    mode.add_argument("--dry-run", action="store_true", help="Print the DDL without running it.")
    mode.add_argument("--execute", action="store_true", help="Run the DDL against Redshift.")
    args = parser.parse_args()

    if not args.manifest.exists():
        parser.error(f"{args.manifest} not found. Run dbt parse first.")

    targets = read_export_targets(args.manifest)
    if args.select:
        wanted = set(args.select)
        targets = [t for t in targets if t.saved_query in wanted]
        missing = wanted - {t.saved_query for t in targets}
        if missing:
            parser.error(f"no export defined for: {', '.join(sorted(missing))}")

    if not targets:
        print("no saved queries with exports found")
        return 0

    if args.list or not (args.dry_run or args.execute):
        print(f"{len(targets)} export target(s)")
        for target in targets:
            print(f"  {target.saved_query}")
            print(f"    -> {target.export_as} {target.qualified_name}")
            print(f"       metrics  {', '.join(target.metrics)}")
            print(f"       group_by {', '.join(target.group_by) or '(none)'}")
        return 0

    executor = None
    if args.execute:
        from app.redshift.client import RedshiftDataApi

        executor = RedshiftDataApi()

    failures = 0
    for target in targets:
        print(f"--- {target.saved_query} -> {target.qualified_name}")
        try:
            statements = build_statements(target, compile_sql(args.manifest, target))
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    compile failed: {type(exc).__name__}: {exc}")
            continue

        if args.dry_run:
            for statement in statements:
                print("\n".join(f"    {line}" for line in statement.splitlines()))
            continue

        try:
            assert executor is not None
            for statement in statements:
                executor.execute(statement)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    execute failed: {type(exc).__name__}: {exc}")
        else:
            print("    materialized")

    print(f"\n{len(targets) - failures}/{len(targets)} target(s) succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
