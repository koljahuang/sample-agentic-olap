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


class RedshiftDataApi:
    def __init__(self) -> None:
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.workgroup = os.getenv("REDSHIFT_WORKGROUP", "medical-poc-wg")
        self.database = os.getenv("REDSHIFT_DATABASE", "medical_dw")
        self.secret_arn = os.getenv("REDSHIFT_SECRET_ARN")
        if not self.secret_arn:
            raise RuntimeError("REDSHIFT_SECRET_ARN is required for policy publication")
        self.client = _shared_client(self.region)

    def execute(self, sql: str, timeout_seconds: int = 55) -> dict[str, Any]:
        response = self.client.execute_statement(
            WorkgroupName=self.workgroup,
            Database=self.database,
            SecretArn=self.secret_arn,
            Sql=sql,
        )
        statement_id = response["Id"]
        deadline = time.monotonic() + timeout_seconds
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
        raise TimeoutError(f"Redshift statement timed out (cancelled): {statement_id}")

    def query(self, sql: str, timeout_seconds: int = 60) -> list[list[Any]]:
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

    def query_records(self, sql: str, timeout_seconds: int = 60) -> dict[str, Any]:
        """Run a query and return column names plus row dicts, ready for JSON."""
        status = self.execute(sql, timeout_seconds=timeout_seconds)
        result = self.client.get_statement_result(Id=status["Id"])
        columns = [col["name"] for col in result.get("ColumnMetadata", [])]
        rows: list[dict[str, Any]] = []
        for record in result.get("Records", []):
            values = [self._field_value(field) for field in record]
            rows.append(dict(zip(columns, values)))
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
