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
