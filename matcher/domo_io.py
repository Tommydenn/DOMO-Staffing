"""Domo API I/O for the crosswalk matcher.

Reads from ADP punches and EM employees, writes the resolved crosswalk
back as a dataset replacement.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class DomoClient:
    host: str
    token: str

    @classmethod
    def from_env(cls) -> "DomoClient":
        host = os.environ["DOMO_API_HOST"]
        token = os.environ["DOMO_DEVELOPER_TOKEN"]
        if not token:
            raise RuntimeError(
                "DOMO_DEVELOPER_TOKEN is empty. Paste a token from "
                "Domo Admin -> Security -> Access Tokens into matcher/.env."
            )
        return cls(host=host, token=token)

    def _headers(self) -> dict[str, str]:
        return {"X-DOMO-Developer-Token": self.token, "Accept": "application/json"}

    def query(self, dataset_id: str, sql: str) -> list[dict]:
        url = f"https://{self.host}/api/query/v1/execute/{dataset_id}"
        with httpx.Client(timeout=120.0) as client:
            r = client.post(
                url, headers=self._headers(), json={"sql": sql, "fillEmptyCells": True}
            )
            r.raise_for_status()
            payload = r.json()
        cols = payload.get("columns", [])
        rows = payload.get("rows", [])
        return [dict(zip(cols, row)) for row in rows]

    def replace_dataset_csv(self, dataset_id: str, rows: list[dict], columns: list[str]) -> None:
        """Overwrite a dataset's contents with a CSV body."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: ("" if row.get(c) is None else row[c]) for c in columns})
        body = buf.getvalue()

        url = f"https://{self.host}/api/data/v3/datasources/{dataset_id}/uploads"
        upload_headers = {**self._headers(), "Content-Type": "text/csv"}
        with httpx.Client(timeout=300.0) as client:
            r = client.put(
                url,
                headers=upload_headers,
                content=body.encode("utf-8"),
                params={"appendData": "false"},
            )
            r.raise_for_status()


def fetch_adp_workers(client: DomoClient, dataset_id: str, days: int = 90) -> list[dict]:
    """Distinct ADP associates with name + most-frequent community in the window."""
    sql = f"""
    SELECT
      `Associate ID` AS adp_associate_id,
      MAX(`Employee Name`) AS adp_name,
      MAX(`Job Title Description`) AS adp_title,
      MAX(`Department Simplified`) AS adp_department,
      MAX(`Community Name`) AS adp_community
    FROM table
    WHERE `Timecard Date` >= DATE_ADD(CURRENT_DATE, -{int(days)})
      AND `Associate ID` IS NOT NULL
      AND `Employee Name` IS NOT NULL AND `Employee Name` != ''
    GROUP BY 1
    """
    return client.query(dataset_id, sql)


def fetch_em_employees(client: DomoClient, dataset_id: str) -> list[dict]:
    """All EM employees (active and inactive). We keep inactive in case of historical matches."""
    sql = """
    SELECT
      ID AS em_employee_id,
      Sort_Name AS em_name,
      First_Name AS em_first_name,
      Last_Name AS em_last_name,
      Title AS em_title,
      Community_ID AS em_community_id,
      LOWER(Inactive) AS em_inactive
    FROM table
    WHERE Sort_Name IS NOT NULL AND Sort_Name != ''
    """
    return client.query(dataset_id, sql)


def fetch_em_active_employee_ids(client: DomoClient, svc_dataset_id: str, days: int = 90) -> set[str]:
    """Employee_IDs that actually appear in Service Received in the window — useful for filtering."""
    sql = f"""
    SELECT DISTINCT Employee_ID AS em_employee_id
    FROM table
    WHERE Service_Date >= DATE_ADD(CURRENT_DATE, -{int(days)})
      AND Employee_ID IS NOT NULL AND Employee_ID != ''
    """
    return {row["em_employee_id"] for row in client.query(svc_dataset_id, sql)}
