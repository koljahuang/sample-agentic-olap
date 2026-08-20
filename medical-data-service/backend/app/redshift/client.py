from __future__ import annotations

import itertools
import os
import time
from functools import lru_cache
from typing import Any

import boto3


@lru_cache(maxsize=1)
def _shared_client(region: str):
    """One boto3 client for the process. Creating a client per request re-loads
    the ECS task-role credentials and adds seconds of cold-start latency."""
    return boto3.client("redshift-data", region_name=region)


# Redshift Serverless compiles each new query shape on a remote, scalable
# compilation service and warms compute on demand. The FIRST time a given SQL
# shape runs on a cold/idle workgroup this can take several MINUTES (observed
# 200-500s) even though the actual execution is milliseconds. MetricFlow emits
# a fresh SQL string per metric/dimension combination, so cache misses (=cold
# compiles) are common. The default here must therefore be generous; override
# with REDSHIFT_QUERY_TIMEOUT if the ALB/client idle timeouts allow more.
DEFAULT_QUERY_TIMEOUT = int(os.getenv("REDSHIFT_QUERY_TIMEOUT", "300"))


class RedshiftTimeout(TimeoutError):
    """Raised when a statement is cancelled after exceeding the timeout.

    Carries the statement id and elapsed seconds so callers can surface a
    clear, actionable message instead of a bare stack trace."""

    def __init__(self, statement_id: str, elapsed: float) -> None:
        self.statement_id = statement_id
        self.elapsed = elapsed
        super().__init__(
            f"Redshift statement timed out after {elapsed:.0f}s and was cancelled "
            f"({statement_id}). This is almost always Redshift Serverless cold-start / "
            f"first-time query compilation, not a data-size problem \u2014 the same query "
            f"usually returns in seconds once its plan is cached. Retry in a moment, or "
            f"raise REDSHIFT_QUERY_TIMEOUT."
        )


class RedshiftDataApi:
    def __init__(self) -> None:
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.workgroup = os.getenv("REDSHIFT_WORKGROUP", "medical-poc-wg")
        self.database = os.getenv("REDSHIFT_DATABASE", "medical_dw")
        self.secret_arn = os.getenv("REDSHIFT_SECRET_ARN")
        if not self.secret_arn:
            raise RuntimeError("REDSHIFT_SECRET_ARN is required for policy publication")
        self.client = _shared_client(self.region)

    def execute(self, sql: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        if timeout_seconds is None:
            timeout_seconds = DEFAULT_QUERY_TIMEOUT
        response = self.client.execute_statement(
            WorkgroupName=self.workgroup,
            Database=self.database,
            SecretArn=self.secret_arn,
            Sql=sql,
        )
        statement_id = response["Id"]
        started = time.monotonic()
        deadline = started + timeout_seconds
        # Poll quickly at first (most queries finish in <2s), then back off, so
        # a fast query returns fast but a cold one does not busy-wait.
        intervals = itertools.chain([0.25, 0.25, 0.5, 0.5], itertools.repeat(1.0))
        while time.monotonic() < deadline:
            status = self.client.describe_statement(Id=statement_id)
            if status["Status"] == "FINISHED":
                return status
            if status["Status"] in {"FAILED", "ABORTED"}:
                raise RuntimeError(status.get("Error", status["Status"]))
            time.sleep(next(intervals))
        # Timed out: cancel the statement so it does not keep running on Redshift
        # and pile up (accumulated stuck statements saturate the workgroup).
        try:
            self.client.cancel_statement(Id=statement_id)
        except Exception:
            pass
        raise RedshiftTimeout(statement_id, time.monotonic() - started)

    def query(self, sql: str, timeout_seconds: int | None = None) -> list[list[Any]]:
        status = self.execute(sql, timeout_seconds=timeout_seconds)
        result = self.client.get_statement_result(Id=status["Id"])
        rows: list[list[Any]] = []
        for record in result.get("Records", []):
            values = []
            for field in record:
                values.append(self._field_value(field))
            rows.append(values)
        return rows

    @staticmethod
    def _field_value(field: dict[str, Any]) -> Any:
        """Redshift Data API wraps every cell as {typeKey: value}; unwrap it.

        A SQL NULL arrives as {"isNull": true}, which we map to Python None.
        """
        if not field or field.get("isNull"):
            return None
        return next(iter(field.values()))

    def query_records(self, sql: str, timeout_seconds: int | None = None) -> dict[str, Any]:
        """Run a query and return column names plus row dicts, ready for JSON."""
        status = self.execute(sql, timeout_seconds=timeout_seconds)
        result = self.client.get_statement_result(Id=status["Id"])
        columns = [col["name"] for col in result.get("ColumnMetadata", [])]
        rows: list[dict[str, Any]] = []
        for record in result.get("Records", []):
            values = [self._field_value(field) for field in record]
            rows.append(dict(zip(columns, values)))
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
