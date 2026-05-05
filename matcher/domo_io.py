"""Domo public API I/O for the crosswalk matcher.

Uses OAuth client-credentials against api.domo.com so we can both query and
write datasets — the dev-token internal API blocks writes for `large-file-upload`
connector datasets.
"""

from __future__ import annotations

import csv
import io
import os
import time
from dataclasses import dataclass

import httpx


_API_BASE = "https://api.domo.com"


@dataclass
class _Token:
    value: str
    expires_at: float


class DomoClient:
    """OAuth client for the Domo public API."""

    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise RuntimeError(
                "DOMO_CLIENT_ID / DOMO_CLIENT_SECRET are required. "
                "Add them as GitHub Actions secrets and to matcher/.env."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: _Token | None = None

    @classmethod
    def from_env(cls) -> "DomoClient":
        return cls(
            os.environ.get("DOMO_CLIENT_ID", ""),
            os.environ.get("DOMO_CLIENT_SECRET", ""),
        )

    # ----- auth -----

    def _bearer(self) -> str:
        if self._token and time.time() < self._token.expires_at - 60:
            return self._token.value
        # Try the specific data scope; if Domo rejects with 400 (scope not granted to
        # this client), retry without scope so the default client scopes apply.
        scopes_to_try = ["data", None]
        last_err: httpx.HTTPStatusError | None = None
        with httpx.Client(timeout=30.0) as client:
            for scope in scopes_to_try:
                form: dict[str, str] = {"grant_type": "client_credentials"}
                if scope:
                    form["scope"] = scope
                r = client.post(
                    f"{_API_BASE}/oauth/token",
                    data=form,
                    auth=(self._client_id, self._client_secret),
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    self._token = _Token(
                        value=data["access_token"],
                        expires_at=time.time() + int(data.get("expires_in", 3600)),
                    )
                    return self._token.value
                if r.status_code in (400, 401, 403):
                    last_err = httpx.HTTPStatusError(
                        f"OAuth token request failed (scope={scope!r}): "
                        f"{r.status_code} {r.text[:300]}",
                        request=r.request, response=r,
                    )
                    continue
                r.raise_for_status()
        if last_err:
            raise last_err
        raise RuntimeError("OAuth token request failed for unknown reason")

    def _headers(self, *, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer()}",
            "Accept": "application/json",
            "Content-Type": content_type,
        }

    # ----- queries -----

    def query(self, dataset_id: str, sql: str) -> list[dict]:
        url = f"{_API_BASE}/v1/datasets/query/execute/{dataset_id}"
        with httpx.Client(timeout=180.0) as client:
            r = client.post(url, headers=self._headers(), json={"sql": sql})
            r.raise_for_status()
            payload = r.json()
        cols = payload.get("columns", [])
        rows = payload.get("rows", [])
        return [dict(zip(cols, row)) for row in rows]

    # ----- dataset lifecycle -----

    def find_dataset_by_name(self, name: str) -> str | None:
        url = f"{_API_BASE}/v1/datasets"
        with httpx.Client(timeout=60.0) as client:
            offset = 0
            while True:
                r = client.get(
                    url,
                    headers=self._headers(),
                    params={"nameLike": name, "limit": 50, "offset": offset, "sort": "name"},
                )
                r.raise_for_status()
                page = r.json()
                if not page:
                    return None
                for ds in page:
                    if ds.get("name") == name:
                        return ds["id"]
                if len(page) < 50:
                    return None
                offset += 50

    def get_dataset_columns(self, dataset_id: str) -> list[dict]:
        url = f"{_API_BASE}/v1/datasets/{dataset_id}"
        with httpx.Client(timeout=30.0) as client:
            r = client.get(url, headers=self._headers())
            r.raise_for_status()
            return r.json().get("schema", {}).get("columns", []) or []

    def create_dataset(self, name: str, description: str, columns: list[dict]) -> str:
        url = f"{_API_BASE}/v1/datasets"
        body = {"name": name, "description": description, "schema": {"columns": columns}}
        with httpx.Client(timeout=60.0) as client:
            r = client.post(url, headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json()["id"]

    def delete_dataset(self, dataset_id: str) -> None:
        url = f"{_API_BASE}/v1/datasets/{dataset_id}"
        with httpx.Client(timeout=30.0) as client:
            r = client.delete(url, headers=self._headers())
            r.raise_for_status()

    def ensure_dataset(self, name: str, description: str, columns: list[dict]) -> str:
        """Find by name or create. Recreates the dataset if schema doesn't match."""
        existing = self.find_dataset_by_name(name)
        if not existing:
            return self.create_dataset(name, description, columns)
        # Compare existing columns to desired columns by (name, type)
        current = self.get_dataset_columns(existing)
        want = {(c["name"], c["type"]) for c in columns}
        have = {(c.get("name"), c.get("type")) for c in current}
        if want == have:
            return existing
        # Schema mismatch — recreate (the data is rebuilt every run anyway)
        self.delete_dataset(existing)
        return self.create_dataset(name, description, columns)

    # ----- writes -----

    def replace_dataset_data_csv(self, dataset_id: str, csv_body: str) -> None:
        url = f"{_API_BASE}/v1/datasets/{dataset_id}/data"
        with httpx.Client(timeout=300.0) as client:
            r = client.put(
                url,
                headers=self._headers(content_type="text/csv"),
                content=csv_body.encode("utf-8"),
            )
            r.raise_for_status()

    def replace_dataset_csv(self, dataset_id: str, rows: list[dict], columns: list[str]) -> None:
        """Build CSV from rows + replace the dataset's contents."""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: ("" if row.get(c) is None else row[c]) for c in columns})
        self.replace_dataset_data_csv(dataset_id, buf.getvalue())

    def append_csv_row(self, dataset_id: str, row: dict, columns: list[str]) -> None:
        """Single-row append for the review-UI's decision logger.

        Domo's PUT /data has no append mode for arbitrary CSVs; emulate by
        reading current contents, adding the row, re-PUTting. Cheap because
        the decisions dataset stays small.
        """
        existing = self.query(dataset_id, "SELECT * FROM table")
        existing.append(row)
        self.replace_dataset_csv(dataset_id, existing, columns)


# ----- domain-specific fetches (unchanged contracts) -----

CROSSWALK_NAME = "MAP | Employee Crosswalk"
CROSSWALK_DESC = "ADP Associate ID <-> Eldermark Employee_ID crosswalk, built daily by the matcher GitHub Action. Source: github.com/Tommydenn/DOMO-Staffing"
CROSSWALK_COLUMNS = [
    {"type": "STRING", "name": "unified_employee_id"},
    {"type": "STRING", "name": "adp_associate_id"},
    {"type": "STRING", "name": "adp_name"},
    {"type": "STRING", "name": "adp_title"},
    {"type": "STRING", "name": "adp_department"},
    {"type": "STRING", "name": "adp_community"},
    {"type": "STRING", "name": "em_employee_id"},
    {"type": "STRING", "name": "em_name"},
    {"type": "STRING", "name": "candidates_json"},
    {"type": "DOUBLE", "name": "confidence"},
    {"type": "STRING", "name": "match_source"},
    {"type": "STRING", "name": "verified_by"},
    {"type": "DATE", "name": "last_verified"},
    {"type": "STRING", "name": "notes"},
]

DECISIONS_NAME = "MAP | Employee Crosswalk Decisions"
DECISIONS_DESC = "Append-only log of human review decisions from the Vercel review UI."
DECISIONS_COLUMNS = [
    {"type": "STRING", "name": "adp_associate_id"},
    {"type": "STRING", "name": "em_employee_id"},
    {"type": "STRING", "name": "decision"},
    {"type": "DATETIME", "name": "decided_at"},
]


def fetch_adp_workers(client: DomoClient, dataset_id: str, days: int = 90) -> list[dict]:
    """Distinct ADP associates with name + most-frequent community in the window."""
    sql = f"""
    SELECT
      `Associate ID` AS adp_associate_id,
      MAX(`Payroll Name`) AS adp_name,
      MAX(`Job Title Description`) AS adp_title,
      MAX(`Department Simplified`) AS adp_department,
      MAX(`Community Name`) AS adp_community
    FROM table
    WHERE `Timecard Date` >= DATE_ADD(CURRENT_DATE, -{int(days)})
      AND `Associate ID` IS NOT NULL
      AND `Payroll Name` IS NOT NULL AND `Payroll Name` != ''
    GROUP BY 1
    """
    return client.query(dataset_id, sql)


def fetch_em_employees(client: DomoClient, dataset_id: str) -> list[dict]:
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
