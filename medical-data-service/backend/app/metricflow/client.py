from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow.protocols.sql_client import SqlEngine
from metricflow.sql.render.redshift import RedshiftSqlPlanRenderer
from metricflow_semantic_interfaces.implementations.metric import PydanticMetricAggregationParams
from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup


class ExplainOnlySqlClient:
    """Renderer-only client used by MetricFlow to produce SQL.

    Execution is deliberately performed by the Data Service after policy checks,
    not by MetricFlow's internal executor.
    """

    @property
    def sql_engine_type(self) -> SqlEngine:
        return SqlEngine.REDSHIFT

    @property
    def sql_plan_renderer(self) -> RedshiftSqlPlanRenderer:
        return RedshiftSqlPlanRenderer()


def _patch_metric_aggregation_params(manifest: PydanticSemanticManifest) -> PydanticSemanticManifest:
    """Backfill aggregation parameters only for legacy manifests.

    dbt 1.12+ model-level semantic configuration emits
    ``metric_aggregation_params`` for simple metrics. Older manifests may not,
    so retain a defensive compatibility path without assuming the deprecated
    ``type_params.measure`` field is populated.
    """
    sales_metrics = {"net_sales_amount", "payment_amount", "quantity", "order_count"}
    patched = []
    for metric in manifest.metrics:
        if metric.type.value != "simple" or metric.type_params.metric_aggregation_params:
            patched.append(metric)
            continue

        # The legacy field may be absent in a dbt 1.12+ manifest. In that case
        # there is nothing safe to infer here; the current manifest should have
        # provided metric_aggregation_params already.
        legacy_measure = metric.type_params.measure
        if legacy_measure is None:
            patched.append(metric)
            continue

        measure_name = legacy_measure.name
        semantic_model = "sales_daily" if measure_name in sales_metrics else "campaign_attribution"
        time_dimension = "stat_date" if semantic_model == "sales_daily" else "order_date"
        params = PydanticMetricAggregationParams(
            semantic_model=semantic_model,
            agg="sum",
            agg_time_dimension=time_dimension,
        )
        patched.append(metric.copy(update={
            "type_params": metric.type_params.copy(update={"metric_aggregation_params": params}),
        }))
    return manifest.copy(update={"metrics": patched})


class MetricFlowClient:
    def __init__(self, manifest_path: str | None = None) -> None:
        path = Path(manifest_path or os.getenv(
            "METRICFLOW_MANIFEST_PATH",
            "../../medical-olap-dbt/target/semantic_manifest.json",
        ))
        if not path.exists():
            raise FileNotFoundError(f"MetricFlow semantic manifest not found: {path}")
        manifest = PydanticSemanticManifest.parse_raw(path.read_text())
        self.manifest = _patch_metric_aggregation_params(manifest)
        self.engine = MetricFlowEngine(SemanticManifestLookup(self.manifest), ExplainOnlySqlClient())

    def _render(self, request: MetricFlowQueryRequest) -> str:
        result = self.engine.explain(request)
        task = result.convert_to_execution_plan_result.execution_plan.tasks[0]
        if not task.sql_statement:
            raise RuntimeError("MetricFlow did not produce a SQL statement")
        return task.sql_statement.without_descriptions.sql

    def explain(self, metric_names: list[str], group_by_names: list[str], limit: int = 1000) -> dict[str, Any]:
        request = MetricFlowQueryRequest.create(
            metric_names=metric_names,
            group_by_names=group_by_names,
            limit=limit,
        )
        return {
            "metrics": metric_names,
            "group_by": group_by_names,
            "sql": self._render(request),
        }

    def saved_query(self, name: str) -> Any:
        for saved_query in self.manifest.saved_queries:
            if saved_query.name == name:
                return saved_query
        known = ", ".join(sorted(q.name for q in self.manifest.saved_queries))
        raise ValueError(f"saved query not found: {name}. Known: {known or '(none)'}")

    def explain_saved_query(self, name: str) -> dict[str, Any]:
        """Render a saved query, so a materialized cache and an ad-hoc query
        cannot disagree about a metric definition.

        No limit is applied: the output is meant to be written to a table.
        """
        saved_query = self.saved_query(name)
        request = MetricFlowQueryRequest.create(saved_query_name=name)
        return {
            "saved_query": name,
            "metrics": list(saved_query.query_params.metrics),
            "group_by": [str(item) for item in saved_query.query_params.group_by],
            "sql": self._render(request),
        }
