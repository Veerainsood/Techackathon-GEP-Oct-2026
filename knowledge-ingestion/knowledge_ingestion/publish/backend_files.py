"""Send files uploaded to HubSpot to the backend, which checks them with the data-prep agent and runs the leak check
(POST {BACKEND_EVENTS_URL}/hubspot/files, signed like sync events). The backend processes each file version once."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotFile
from knowledge_ingestion.publish.backend_events import _sign

TIMEOUT_S = 900  # data prep in the sandbox, then the leak check


def submit(file: HubSpotFile, content: bytes) -> dict[str, Any]:
    backend = config.backend
    body = json.dumps({"tenant_id": backend.tenant_id, "source": "hubspot",
                       "file": {"id": file.id, "name": file.name, "updated_at": file.updated_at, "size": file.size},
                       "content_b64": base64.b64encode(content).decode("ascii")}).encode("utf-8")
    response = httpx.post(f"{backend.events_url}/hubspot/files", content=body, timeout=TIMEOUT_S,
                          headers={"Content-Type": "application/json", "X-Backend-Signature": _sign(body, backend.secret)})
    response.raise_for_status()
    return response.json()
