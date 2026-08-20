"""Remote MCP server for the medical sales data warehouse.

Exposed over Streamable HTTP so a local agent (Amazon Q, Claude Desktop, ...)
can connect to it as a remote MCP host and answer questions in natural
language. No authentication or permission control: every tool is open.

Tools:
  - list_metrics       what can be measured
  - list_dimensions    how it can be sliced
  - preview_sql        the SQL a query would run (no execution)
  - run_query          execute a metric query on Redshift and return rows
"""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings

from app.identity import current_caller
from app.mcp_auth import CognitoTokenVerifier, caller_from_access_token
from app.redshift.client import RedshiftTimeout
from app.service import data_service, execution_enabled

logger = logging.getLogger("mcp.caller")

OAUTH_ENABLED = os.getenv("MCP_OAUTH_ENABLED", "false").lower() == "true"

_common = dict(
    instructions=(
        "医药销售数据仓库的语义层查询服务。用 list_metrics / list_dimensions "
        "了解可用的指标和维度，再用 run_query 取数、用 preview_sql 只看 SQL。"
        "指标如 net_sales_amount（净销售额）、payment_amount（回款）、roi；"
        "维度如 sales_region__sales_region_name（大区）、product__therapy_area（治疗领域）。"
    ),
    stateless_http=True,
    json_response=True,
    # Behind an ALB the Host header is the public domain, not localhost. The
    # MCP DNS-rebinding guard would reject it, so disable it here.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

if OAUTH_ENABLED:
    # OAuth Resource Server: agents present a Cognito access token (Bearer).
    _issuer = os.environ["COGNITO_ISSUER"]
    _resource = os.environ["MCP_RESOURCE_URL"]  # e.g. https://datak.kolya.icu/mcp
    mcp = FastMCP(
        "medical-data-service",
        token_verifier=CognitoTokenVerifier(),
        auth=AuthSettings(
            issuer_url=_issuer,
            resource_server_url=_resource,
            required_scopes=[],  # any valid Cognito access token from this pool
        ),
        **_common,
    )
else:
    mcp = FastMCP("medical-data-service", **_common)
# Serve at the mount root so the public URL is a clean /mcp (see main.py).
mcp.settings.streamable_http_path = "/"


@mcp.tool()
def list_metrics() -> list[dict[str, str]]:
    """List all metrics. Each item has: name, description, and ``dimensions`` —
    a comma-separated string of the dimensions valid for that metric.

    IMPORTANT: valid dimensions are metric-specific. Only group a metric by a
    name that appears in that metric's own ``dimensions``. For example
    ``campaign_cost`` has no join to ``product__therapy_area``, so it is not
    listed there and must not be used with it. Always use the exact names given
    (the ``entity__dimension`` form, e.g. ``product__therapy_area``), never a
    bare name like ``therapy_area``.
    """
    return data_service().list_metrics()


@mcp.tool()
def list_dimensions() -> list[dict[str, str]]:
    """Global catalogue of group-by dimensions, in canonical form
    (``entity__dimension``, e.g. ``product__therapy_area``).

    Whether a given metric can use a dimension is metric-specific — check the
    metric's own ``dimensions`` list from list_metrics before querying.
    """
    return data_service().list_dimensions()


@mcp.tool()
def preview_sql(metrics: list[str], group_by: list[str] | None = None, limit: int = 1000) -> dict[str, Any]:
    """Render the SQL for a metric query without running it.

    Args:
        metrics: metric names, e.g. ["net_sales_amount", "order_count"].
        group_by: dimensions to slice by, e.g. ["sales_region__sales_region_name"].
        limit: max rows.
    """
    return data_service().generate_sql(metrics, group_by or [], limit)


@mcp.tool()
def run_query(metrics: list[str], group_by: list[str] | None = None, limit: int = 1000) -> dict[str, Any]:
    """Run a metric query on Redshift and return the rows.

    Args:
        metrics: metric names, e.g. ["net_sales_amount"].
        group_by: dimensions to slice by, e.g. ["sales_region__sales_region_name"].
        limit: max rows (capped at 10000).

    Returns the generated SQL plus {columns, rows, row_count}.
    """
    # Identity of the caller. In OAuth mode it comes from the live Cognito
    # access token; in API-key mode it is the key's creator.
    if OAUTH_ENABLED:
        from mcp.server.auth.middleware.auth_context import get_access_token
        who = caller_from_access_token(get_access_token())
    else:
        caller = current_caller()
        who = caller.email if caller else "anonymous"
    logger.info("run_query by=%s metrics=%s group_by=%s", who, metrics, group_by)
    try:
        if not execution_enabled():
            return {
                "error": "query execution is disabled (set QUERY_EXECUTION_ENABLED=true)",
                "caller": who,
                **data_service().generate_sql(metrics, group_by or [], limit),
            }
        return {"caller": who, **data_service().run_query(metrics, group_by or [], limit)}
    except RedshiftTimeout as exc:
        # Graceful degradation for the most common failure: Redshift Serverless
        # cold-start / first-time query compilation blowing past our timeout.
        # Return a definitive, self-explanatory result (with the SQL) so the
        # agent tells the user *why* and suggests a retry, instead of looping
        # or hanging.
        logger.info("run_query timeout by=%s stmt=%s elapsed=%.0fs",
                    who, exc.statement_id, exc.elapsed)
        hint = (
            "查询超时并非因为数据量，而是 Redshift Serverless 的冷启动 / 首次查询编译"
            "（同样的查询在计划被缓存后通常几秒即可返回）。请稍等片刻后重试；若经常发生，"
            "可调大 REDSHIFT_QUERY_TIMEOUT 或对工作组做保活预热。"
        )
        result: dict[str, Any] = {"caller": who, "error": str(exc), "reason": "redshift_cold_start_timeout",
                                  "retryable": True, "hint": hint,
                                  "metrics": metrics, "group_by": group_by or []}
        try:  # include the SQL for transparency; generating it is cheap and offline
            result.update(data_service().generate_sql(metrics, group_by or [], limit))
        except Exception:  # noqa: BLE001
            pass
        return result
    except Exception as exc:  # noqa: BLE001
        # Return a clean, final error instead of raising, so the agent gets a
        # definitive "not possible" and does not loop retrying variations.
        msg = str(exc)
        hint = None
        if "join path" in msg or "group-by-item" in msg:
            hint = (
                "该指标与该维度之间没有连接路径，无法这样分组。常见于把只存在于某个事实表的"
                "指标（如 campaign_cost 只在推广触点表）按另一维度（如 product__therapy_area）"
                "分组。换一个两者都支持的维度，或改用其他指标。"
            )
        logger.info("run_query failed by=%s error=%s", who, msg[:200])
        return {"caller": who, "error": msg[:500], "hint": hint, "retryable": False,
                "metrics": metrics, "group_by": group_by or []}


# ---------------------------------------------------------------------------
# Raw data exploration (bypasses the semantic layer). Useful for inspecting the
# warehouse directly: schemas, tables, columns, and a small row sample.
# ---------------------------------------------------------------------------
@mcp.tool()
def list_schemas() -> dict[str, Any]:
    """List the analytics schemas in the warehouse (system schemas hidden)."""
    return data_service().list_schemas()


@mcp.tool()
def list_tables(schema: str | None = None) -> dict[str, Any]:
    """List tables and views. Optionally pass a schema (e.g. 'dwd') to filter."""
    return data_service().list_tables(schema)


@mcp.tool()
def describe_table(schema: str, table: str) -> dict[str, Any]:
    """List a table's columns and data types. Args: schema, table."""
    return data_service().describe_table(schema, table)


@mcp.tool()
def sample_table(schema: str, table: str, limit: int = 50) -> dict[str, Any]:
    """Return up to 50 raw rows from a table for inspection (limit capped at 50).

    This reads the table directly, bypassing metric definitions. For business
    numbers prefer run_query (it applies the semantic-layer口径). Args: schema,
    table, limit (<=50).
    """
    if not execution_enabled():
        return {"error": "query execution is disabled (set QUERY_EXECUTION_ENABLED=true)"}
    return data_service().sample_table(schema, table, limit)


def run() -> None:
    """Run standalone over Streamable HTTP (for local dev without FastAPI)."""
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    run()
