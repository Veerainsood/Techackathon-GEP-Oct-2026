"""HTTP API for the knowledge ingestion pipeline.

Run from the project root:
    .\.venv\Scripts\python.exe -m uvicorn knowledge_ingestion.api.app:app --port 8000

On start, the API polls HubSpot every POLL_INTERVAL_SECONDS (unless POLL_ENABLED=false).

Endpoints:
    GET  /health                         liveness check
    POST /hubspot/sync                   poll HubSpot now: store records changed since the last run,
                                         and return what was imported
    GET  /collections                    recent poll runs, with name, start and end times, and counts
    GET  /knowledge/records              latest stored snapshot of each record, for the agent
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotError
from knowledge_ingestion.pipeline.hubspot_sync import SOURCE_SYSTEM, collection_name, sync_hubspot
from knowledge_ingestion.storage.factory import get_store
from knowledge_ingestion.storage.warehouse import get_warehouse

logger = logging.getLogger("knowledge_ingestion")


async def _poll_forever(interval_seconds: int) -> None:
    while True:
        try:
            await asyncio.to_thread(sync_hubspot)
        except Exception:
            # The run is already recorded as failed, so log and keep polling.
            logger.exception("Scheduled HubSpot poll failed")
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    get_store()  # creates the knowledge tables
    get_warehouse()  # checks the techackathon tables and prepares hubspot.contacts
    poller: asyncio.Task[None] | None = None
    if config.polling.enabled:
        poller = asyncio.create_task(_poll_forever(config.polling.interval_seconds))
    try:
        yield
    finally:
        if poller is not None:
            poller.cancel()


app = FastAPI(title="Knowledge Ingestion API", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/hubspot/sync")
def hubspot_sync() -> dict[str, Any]:
    try:
        result = sync_hubspot()
    except HubSpotError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    return {
        "run_id": result.run_id,
        "collection_name": result.collection_name,
        "source_system": SOURCE_SYSTEM,
        "status": "succeeded",
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "counts": {object_type: len(items) for object_type, items in result.records.items()},
        "imported": {
            object_type: [{"id": item.id, "properties": item.properties} for item in items]
            for object_type, items in result.records.items()
        },
    }


@app.get("/collections")
def collections(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    runs = get_store().list_runs(SOURCE_SYSTEM, limit)
    return {
        "source_system": SOURCE_SYSTEM,
        "count": len(runs),
        "collections": [
            {
                "collection_name": collection_name(run["started_at"]),
                "run_id": run["run_id"],
                "status": run["status"],
                "started_at": run["started_at"].isoformat() if run["started_at"] else None,
                "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
                "counts": run["counts"],
                "error": run["error"],
            }
            for run in runs
        ],
    }


@app.get("/knowledge/records")
def knowledge_records(
    object_type: str = Query("contacts", pattern="^(contacts|companies)$"),
    source: str = Query(SOURCE_SYSTEM),
    limit: int = Query(100, ge=1, le=1000),
) -> dict[str, Any]:
    records = get_store().latest_records(source, object_type, limit)
    return {
        "source_system": source,
        "object_type": object_type,
        "count": len(records),
        "records": records,
    }
