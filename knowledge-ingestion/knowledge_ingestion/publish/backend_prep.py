"""Check HubSpot batches with the backend's data-prep agent before they are loaded into the warehouse.

- check_batch() sends the changed records to POST {BACKEND_EVENTS_URL}/prep/batches. Records that only needed safe fixes
  come back cleaned and are loaded now; records touched by a fix that needs a person's approval are held by the backend.
- released() returns held records whose fixes have all been decided (approved fixes applied); they are loaded on the next
  poll and acknowledged with ack().

Calls are signed like sync events (HMAC-SHA256 of the body with BACKEND_EVENT_SECRET, header X-Backend-Signature).
A failed check raises, so the run fails, the cursor does not move, and the same records are checked again next poll.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotRecord
from knowledge_ingestion.publish.backend_events import _sign

SOURCE = "hubspot"
TIMEOUT_S = 600  # the agent may investigate in the sandbox before answering


@dataclass
class ReleasedBatch:
    job_id: str
    object_type: str
    records: list[HubSpotRecord]


def _post(path: str, payload: dict[str, Any]) -> Any:
    backend = config.backend
    body = json.dumps(payload, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Backend-Signature": _sign(body, backend.secret)}
    response = httpx.post(f"{backend.events_url}{path}", content=body, headers=headers, timeout=TIMEOUT_S)
    response.raise_for_status()
    return response.json()


def _record(row: dict[str, Any]) -> HubSpotRecord:
    properties = {k: v for k, v in row.items() if k != "id" and not k.startswith("_duplicate")}
    return HubSpotRecord(str(row["id"]), properties)


def check_batch(object_type: str, records: list[HubSpotRecord]) -> tuple[list[HubSpotRecord], set[str]]:
    """Returns (records to load now, ids held for approval)."""
    if not config.backend.enabled or not records:
        return records, set()
    result = _post("/prep/batches", {"tenant_id": config.backend.tenant_id, "source": SOURCE, "object_type": object_type,
                                     "records": [{"id": r.id, "properties": r.properties} for r in records]})
    return [_record(r) for r in result["records"]], set(result.get("held_ids") or [])


def released() -> list[ReleasedBatch]:
    if not config.backend.enabled:
        return []
    rows = _post("/prep/batches/released", {"tenant_id": config.backend.tenant_id, "source": SOURCE})
    return [ReleasedBatch(r["job_id"], r["object_type"], [_record(x) for x in r["records"]]) for r in rows]


def ack(job_id: str) -> None:
    _post("/prep/batches/ack", {"tenant_id": config.backend.tenant_id, "job_id": job_id})
