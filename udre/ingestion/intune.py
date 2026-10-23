"""Microsoft Intune connector (Microsoft Graph v1.0, read-only, app-only OAuth2 client credentials).

Resources (all v1.0):
  managed_devices                   GET /deviceManagement/managedDevices
  compliance_policies               GET /deviceManagement/deviceCompliancePolicies
  compliance_policy_device_statuses GET /deviceManagement/deviceCompliancePolicies/{id}/deviceStatuses (per policy)
  detected_apps                     GET /deviceManagement/detectedApps
  report:<ReportName>               POST /deviceManagement/reports/exportJobs, poll, download CSV (optional)

Required application permissions (admin consent): DeviceManagementManagedDevices.Read.All,
DeviceManagementConfiguration.Read.All.  The export-jobs resource additionally creates a server-side job
(read-only data, but a POST); it is disabled unless explicitly listed in ``resources``.

Incremental sync: these v1.0 collections do not offer delta queries, so every resource uses full refresh
with reconciliation (rows no longer returned get ``_udre_present = false``).  Server-side timestamp
filtering is not relied on because its support varies by resource and tenant.
"""

from __future__ import annotations

import csv
import io
import os
import time
import zipfile
from typing import Any, Callable, Dict, List, Optional

import httpx

from udre.contracts.models import SourceType
from udre.ingestion.base import (
    ApiClient,
    AuthenticationError,
    ConnectorCapabilities,
    ConnectorError,
    FetchResult,
    ObjectSpec,
    RelationshipHint,
    SourceConnector,
)

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com"
RESOURCES = {
    "managed_devices": ("/deviceManagement/managedDevices", "Intune managed devices: ownership, OS, compliance, last sync, user"),
    "compliance_policies": ("/deviceManagement/deviceCompliancePolicies", "Device compliance policies"),
    "compliance_policy_device_statuses": ("/deviceManagement/deviceCompliancePolicies/{id}/deviceStatuses",
                                          "Per-device status for each compliance policy"),
    "detected_apps": ("/deviceManagement/detectedApps", "Applications detected on managed devices"),
}
DEFAULT_RESOURCES = ["managed_devices", "compliance_policies", "compliance_policy_device_statuses", "detected_apps"]


class MicrosoftIntuneSourceAdapter(SourceConnector):
    """Connector feeding the common ingestion pipeline (the queryable adapter is the snapshot it produces)."""

    source_type = SourceType.REST_API

    def __init__(self, tenant_id: Optional[str] = None, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 resources: Optional[List[str]] = None, graph_base: str = GRAPH_V1, authority: str = AUTHORITY,
                 page_size: int = 100, transport: Optional[httpx.BaseTransport] = None,
                 sleep: Callable[[float], None] = time.sleep, report_poll_s: float = 2.0, report_timeout_s: float = 300.0):
        self._tenant = tenant_id or os.environ.get("UDRE_INTUNE_TENANT_ID", "")
        self._client_id = client_id or os.environ.get("UDRE_INTUNE_CLIENT_ID", "")
        self._secret = client_secret or os.environ.get("UDRE_INTUNE_CLIENT_SECRET", "")
        if not (self._tenant and self._client_id and self._secret):
            raise AuthenticationError("Intune connector requires tenant id, client id and client secret "
                                      "(UDRE_INTUNE_TENANT_ID / UDRE_INTUNE_CLIENT_ID / UDRE_INTUNE_CLIENT_SECRET)")
        self.resources = resources or DEFAULT_RESOURCES
        unknown = [r for r in self.resources if r not in RESOURCES and not r.startswith("report:")]
        if unknown:
            raise ValueError(f"unsupported Intune resources {unknown}")
        self.graph_base = graph_base.rstrip("/")
        self.authority = authority.rstrip("/")
        self.page_size = page_size
        self.sleep = sleep
        self.report_poll_s, self.report_timeout_s = report_poll_s, report_timeout_s
        self._token: Optional[str] = None
        self._token_exp = 0.0
        self._http = httpx.Client(transport=transport, timeout=30)
        self.api = ApiClient(self.graph_base, self._get_token, self.secrets, transport=transport, sleep=sleep)

    # ------------------------------------------------------------------ auth
    def secrets(self) -> List[str]:
        return [s for s in (self._secret, self._token, self._tenant, self._client_id) if s]

    def _get_token(self, force: bool = False) -> str:
        if self._token and not force and time.time() < self._token_exp - 60:
            return self._token
        url = f"{self.authority}/{self._tenant}/oauth2/v2.0/token"
        resp = self._http.post(url, data={"client_id": self._client_id, "client_secret": self._secret, "grant_type": "client_credentials",
                                          "scope": "https://graph.microsoft.com/.default"})
        if resp.status_code != 200:
            body = resp.text[:300]
            for s in self.secrets():
                body = body.replace(s, "***")
            raise AuthenticationError(f"Entra ID token request failed (HTTP {resp.status_code}): {body}")
        data = resp.json()
        self._token, self._token_exp = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def authenticate(self) -> None:
        self._get_token(force=True)

    # ------------------------------------------------------------------ discovery
    def discover_objects(self) -> List[ObjectSpec]:
        specs = []
        for r in self.resources:
            if r.startswith("report:"):
                name = "report_" + r.split(":", 1)[1].lower()
                specs.append(ObjectSpec(name=name, primary_key="row_key", description=f"Intune report export {r.split(':', 1)[1]}",
                                        limitations=["export job snapshot; no incremental semantics"]))
            else:
                specs.append(ObjectSpec(name=r, description=RESOURCES[r][1],
                                        limitations=["v1.0 has no delta query for this resource; full refresh with reconciliation"]))
        return specs

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            live_query=False, snapshot=True, incremental_objects=[], full_refresh_objects=list(self.resources),
            api_version="Microsoft Graph v1.0",
            required_permissions=["DeviceManagementManagedDevices.Read.All", "DeviceManagementConfiguration.Read.All"],
            limitations=["Snapshot only: arbitrary SQL runs on the synchronized copy, not on Graph",
                         "No delta queries on these v1.0 Intune collections; each sync is a full refresh",
                         "compliance_policy_device_statuses has no managed device id in v1.0; links to devices are name-based candidates only"])

    # ------------------------------------------------------------------ fetch
    def _paged(self, path: str) -> (List[Dict[str, Any]], int):
        url: Optional[str] = path
        params: Optional[Dict[str, Any]] = {"$top": self.page_size}
        out, pages = [], 0
        while url:
            data = self.api.get_json(url, params=params)
            out.extend(data.get("value", []))
            pages += 1
            url, params = data.get("@odata.nextLink"), None  # nextLink already carries the query
        return out, pages

    def fetch(self, spec: ObjectSpec, checkpoint: Optional[str], full: bool = False) -> FetchResult:
        if spec.name == "compliance_policy_device_statuses":
            policies, pages = self._paged(RESOURCES["compliance_policies"][0])
            rows: List[Dict[str, Any]] = []
            for p in policies:
                statuses, n = self._paged(RESOURCES[spec.name][0].format(id=p["id"]))
                pages += n
                for s in statuses:
                    rows.append(dict(s, id=f"{p['id']}:{s.get('id')}", status_id=s.get("id"), policy_id=p["id"]))
            return FetchResult(records=rows, mode="full", pages=pages)
        if spec.name.startswith("report_"):
            return self._export_report(spec)
        records, pages = self._paged(RESOURCES[spec.name][0])
        for r in records:
            r.pop("@odata.etag", None)
        return FetchResult(records=records, mode="full", pages=pages)

    def _export_report(self, spec: ObjectSpec) -> FetchResult:
        report = next(r.split(":", 1)[1] for r in self.resources if r.startswith("report:") and spec.name.endswith(r.split(":", 1)[1].lower()))
        job = self.api.post_json("/deviceManagement/reports/exportJobs", {"reportName": report, "format": "csv"})
        deadline = time.time() + self.report_timeout_s
        while job.get("status") != "completed":
            if job.get("status") == "failed" or time.time() > deadline:
                raise ConnectorError(f"report export {report} did not complete (status {job.get('status')})")
            self.sleep(self.report_poll_s)
            job = self.api.get_json(f"/deviceManagement/reports/exportJobs('{job['id']}')")
        blob = self._http.get(job["url"]).content  # pre-authenticated download URL
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            rows = list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))
        for i, r in enumerate(rows):
            r["row_key"] = str(i)
        return FetchResult(records=rows, mode="full", pages=1)

    def relationship_hints(self, specs: List[ObjectSpec]) -> List[RelationshipHint]:
        names = {s.name for s in specs}
        if {"compliance_policy_device_statuses", "compliance_policies"} <= names:
            return [RelationshipHint("compliance_policy_device_statuses", "policy_id", "compliance_policies", "id",
                                     {"origin": "graph_parent_child_resource"})]
        return []
