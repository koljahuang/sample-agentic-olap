"""Smoke tests for the MCP tools and the REST surface (no Redshift needed)."""

from fastapi.testclient import TestClient

from app.main import app
from app.service import data_service


def test_catalog_lists_metrics_and_dimensions() -> None:
    ds = data_service()
    metrics = ds.list_metrics()
    dims = ds.list_dimensions()
    assert any(m["name"] == "net_sales_amount" for m in metrics)
    assert any(d["name"] == "sales_region__sales_region_name" for d in dims)


def test_generate_sql_hits_the_atomic_fact() -> None:
    result = data_service().generate_sql(
        ["net_sales_amount"], ["sales_region__sales_region_name"]
    )
    assert "fct_sales_order_item" in result["sql"]


# No lifespan context manager: these requests short-circuit at the middleware
# or the auth dependency, so the one-shot MCP session manager must not be
# started (running it twice across tests raises).
_client = TestClient(app)


def test_config_is_public_but_info_requires_auth() -> None:
    # /api/config is public (SPA needs it before login)
    assert _client.get("/api/config").status_code == 200
    # /api/info now requires a Cognito token
    assert _client.get("/api/info").status_code == 401


def test_mcp_requires_api_key() -> None:
    # No API key -> rejected before reaching the MCP app
    assert _client.get("/mcp").status_code == 401
    assert _client.post("/mcp", headers={"X-API-Key": "bogus"}).status_code == 401


def test_mcp_tools_are_registered() -> None:
    import asyncio

    from app.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == {
        "list_metrics",
        "list_dimensions",
        "preview_sql",
        "run_query",
        "list_schemas",
        "list_tables",
        "describe_table",
        "sample_table",
    }
