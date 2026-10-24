"""Read-only HubSpot CRM client.

Only read requests are made. Lists use GET. The search API uses POST, because
HubSpot requires it for filtered queries, but it never writes to the CRM.
Pagination is followed until every record is fetched.
The access token is sent in the Authorization header and is never logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from knowledge_ingestion.config import config

BASE_URL = "https://api.hubapi.com"
PAGE_SIZE = 100
SEARCH_RESULT_LIMIT = 10_000  # HubSpot's search API returns at most this many matches
REQUEST_TIMEOUT_SECONDS = 30.0


class HubSpotError(RuntimeError):
    """A HubSpot API call failed."""


class HubSpotScopeMissing(HubSpotError):
    """The private app's token lacks a scope the call needs (e.g. files)."""


@dataclass
class HubSpotFile:
    id: str
    name: str
    extension: str
    updated_at: str
    size: int = 0


@dataclass
class HubSpotRecord:
    id: str
    properties: dict[str, Any] = field(default_factory=dict)


class HubSpotClient:
    def __init__(self, access_token: str | None = None) -> None:
        token = access_token if access_token is not None else config.hubspot.access_token
        if not token:
            raise HubSpotError("HUBSPOT_ACCESS_TOKEN is empty in .env")
        self._http = httpx.Client(
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "HubSpotClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def list_all(self, object_type: str, properties: list[str]) -> list[HubSpotRecord]:
        """Return every record of `object_type`, following pagination."""
        records: list[HubSpotRecord] = []
        after: str | None = None

        while True:
            params: dict[str, str] = {
                "limit": str(PAGE_SIZE),
                "properties": ",".join(properties),
            }
            if after:
                params["after"] = after

            response = self._http.get(f"/crm/v3/objects/{object_type}", params=params)
            if response.status_code != 200:
                raise HubSpotError(
                    f"HubSpot {object_type} request failed: "
                    f"{response.status_code} {response.reason_phrase}\n{response.text}"
                )

            payload = response.json()
            for item in payload.get("results", []):
                records.append(
                    HubSpotRecord(id=str(item["id"]), properties=item.get("properties", {}))
                )

            after = payload.get("paging", {}).get("next", {}).get("after")
            if not after:
                return records

    def files_updated_since(self, since_ms: int, extensions: tuple[str, ...] = ("csv", "xlsx", "xls")) -> list[HubSpotFile]:
        """Files in HubSpot's file manager updated at or after `since_ms`, oldest first (read-only; needs the files scope)."""
        from datetime import datetime, timezone

        since_iso = datetime.fromtimestamp(since_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        files: list[HubSpotFile] = []
        after: str | None = None
        filtered = True
        while True:
            params: dict[str, str] = {"limit": str(PAGE_SIZE), "sort": "updatedAt"}
            if filtered:
                params["updatedAtGte"] = since_iso
            if after:
                params["after"] = after
            response = self._http.get("/files/v3/files/search", params=params)
            if response.status_code == 403:
                raise HubSpotScopeMissing(f"HubSpot files need the 'files' scope on the private app: {response.text[:300]}")
            if response.status_code == 400 and filtered and after is None:
                filtered = False  # this portal rejects the date filter: list everything and filter here
                continue
            if response.status_code != 200:
                raise HubSpotError(f"HubSpot files search failed: {response.status_code} {response.reason_phrase}\n{response.text}")
            payload = response.json()
            for item in payload.get("results", []):
                ext = str(item.get("extension") or str(item.get("name", "")).rsplit(".", 1)[-1]).lower()
                updated = str(item.get("updatedAt") or item.get("createdAt") or "")
                if item.get("archived") or ext not in extensions:
                    continue
                name = str(item.get("name") or item["id"])
                if not name.lower().endswith("." + ext):
                    name = f"{name}.{ext}"
                files.append(HubSpotFile(str(item["id"]), name, ext, updated, int(item.get("size") or 0)))
            after = payload.get("paging", {}).get("next", {}).get("after")
            if not after:
                break

        def ms(value: str) -> int:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000) if value else 0

        return sorted((f for f in files if ms(f.updated_at) >= since_ms), key=lambda f: ms(f.updated_at))

    def download_file(self, file_id: str) -> bytes:
        """The file's content, through a short-lived signed URL (read-only)."""
        response = self._http.get(f"/files/v3/files/{file_id}/signed-url")
        if response.status_code == 403:
            raise HubSpotScopeMissing(f"HubSpot files need the 'files' scope on the private app: {response.text[:300]}")
        if response.status_code != 200:
            raise HubSpotError(f"HubSpot signed URL for file {file_id} failed: {response.status_code} {response.text[:300]}")
        content = httpx.get(response.json()["url"], timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
        if content.status_code != 200:
            raise HubSpotError(f"Downloading HubSpot file {file_id} failed: {content.status_code}")
        return content.content

    def search_modified_since(
        self,
        object_type: str,
        properties: list[str],
        modified_property: str,
        since_ms: int,
    ) -> list[HubSpotRecord]:
        """Return records whose `modified_property` is at or after `since_ms` (epoch milliseconds), oldest first."""
        records: list[HubSpotRecord] = []
        after: str | None = None

        while True:
            body: dict[str, Any] = {
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": modified_property,
                                "operator": "GTE",
                                "value": str(since_ms),
                            }
                        ]
                    }
                ],
                "sorts": [{"propertyName": modified_property, "direction": "ASCENDING"}],
                "properties": properties,
                "limit": PAGE_SIZE,
            }
            if after:
                body["after"] = after

            response = self._http.post(f"/crm/v3/objects/{object_type}/search", json=body)
            if response.status_code != 200:
                raise HubSpotError(
                    f"HubSpot {object_type} search failed: "
                    f"{response.status_code} {response.reason_phrase}\n{response.text}"
                )

            payload = response.json()
            if payload.get("total", 0) > SEARCH_RESULT_LIMIT:
                # Paging stops at 10,000 results, so a larger window would silently lose records.
                raise HubSpotError(
                    f"{payload['total']} {object_type} changed since the last run, which is more than "
                    f"HubSpot's search limit of {SEARCH_RESULT_LIMIT}. Poll more often."
                )

            for item in payload.get("results", []):
                records.append(
                    HubSpotRecord(id=str(item["id"]), properties=item.get("properties", {}))
                )

            after = payload.get("paging", {}).get("next", {}).get("after")
            if not after:
                return records
