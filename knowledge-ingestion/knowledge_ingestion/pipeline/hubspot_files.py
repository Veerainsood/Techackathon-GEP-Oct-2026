"""Poll HubSpot's file manager for CSV / Excel files updated since the last run and hand each one to the backend, which
checks it with the data-prep agent and reads it as invoices for the leak check (knowledge_ingestion.publish.backend_files).

The cursor is the newest updatedAt seen; the next poll re-reads a short overlap, and the backend ignores file versions it has
already processed. Without the private app's files scope this step is skipped with a warning; contacts and companies keep
syncing. A file the backend fails on is retried on the next poll (the cursor does not pass it).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotClient, HubSpotScopeMissing
from knowledge_ingestion.publish import backend_files
from knowledge_ingestion.storage.base import KnowledgeStore
from knowledge_ingestion.storage.factory import get_store

logger = logging.getLogger("knowledge_ingestion")

SOURCE_SYSTEM = "hubspot_files"
OVERLAP_MS = 2 * 60 * 1000


def enabled() -> bool:
    return config.backend.enabled and os.environ.get("HUBSPOT_SYNC_FILES", "1") == "1"


def _ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000) if value else 0


def sync_files(store: KnowledgeStore | None = None, client: Any = None) -> dict[str, Any]:
    if not enabled():
        return {"skipped": "file sync disabled or no backend configured"}
    store = store or get_store()
    cursor = store.get_cursor(SOURCE_SYSTEM, "files")
    since_ms = max(0, (cursor or 0) - OVERLAP_MS)
    own = client is None
    client = client or HubSpotClient()
    out: dict[str, Any] = {"files": [], "errors": []}
    newest = cursor
    try:
        try:
            files = client.files_updated_since(since_ms)
        except HubSpotScopeMissing as exc:
            logger.warning("Skipping HubSpot files: %s", exc)
            return {"skipped": "the HubSpot private app has no 'files' scope"}
        for f in files:
            try:
                result = backend_files.submit(f, client.download_file(f.id))
                out["files"].append({"name": f.name, **{k: result.get(k) for k in ("duplicate", "leakage")}})
                newest = max(newest or 0, _ms(f.updated_at))
            except Exception as exc:  # retried next poll: the cursor stops before this file
                logger.exception("HubSpot file %s could not be processed", f.name)
                out["errors"].append({"name": f.name, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                break
    finally:
        if own:
            client.close()
    if newest and newest != cursor:
        run_id = store.start_run(SOURCE_SYSTEM)
        store.save_snapshot(run_id, SOURCE_SYSTEM, datetime.now(timezone.utc), {}, cursors={"files": newest})
    return out
