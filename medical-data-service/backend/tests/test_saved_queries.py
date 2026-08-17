import pytest

from app.metricflow.client import MetricFlowClient


@pytest.fixture(scope="module")
def client() -> MetricFlowClient:
    return MetricFlowClient()


def test_saved_queries_are_registered(client: MetricFlowClient) -> None:
    names = {saved_query.name for saved_query in client.manifest.saved_queries}
    assert "campaign_roi_monthly" in names
    assert "payment_rate_monthly_by_region" in names


def test_unknown_saved_query_lists_the_known_ones(client: MetricFlowClient) -> None:
    with pytest.raises(ValueError, match="saved query not found"):
        client.saved_query("no_such_query")


def test_roi_cache_reads_cost_at_its_native_grain(client: MetricFlowClient) -> None:
    sql = client.explain_saved_query("campaign_roi_monthly")["sql"]
    # Revenue comes from the attribution bridge, which fans out per touchpoint.
    assert '"dwd"."fct_campaign_attribution"' in sql
    # Cost must come from the touchpoint fact, or the ROI denominator inflates.
    assert '"dwd"."fct_campaign_touchpoint"' in sql
    assert "SUM(cost_amount) AS campaign_cost" in sql


def test_payment_rate_cache_matches_the_metric_definition(client: MetricFlowClient) -> None:
    sql = client.explain_saved_query("payment_rate_monthly_by_region")["sql"]
    assert '"dwd"."fct_payment"' in sql
    assert '"dwd"."fct_sales_order_item"' in sql
    # The cache inherits the metric's own CANCELLED exclusion.
    assert "order_item__order_status = 'COMPLETED'" in sql


def test_cached_order_count_still_de_duplicates(client: MetricFlowClient) -> None:
    sql = client.explain_saved_query("sales_daily_by_region")["sql"]
    assert "COUNT(DISTINCT" in sql
