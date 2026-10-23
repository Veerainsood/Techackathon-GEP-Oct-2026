"""Publish HubSpot sync results to the backend as backend.sync-event.v1 events (POST {BACKEND_EVENTS_URL}/events/sync).

How it works:
- Each sync builds its events and queues them in backend_outbox, in the same transaction as the run.
  A run that changed nothing queues no event.
- deliver_pending() sends queued events in order, after each sync. A failed send stays queued and is
  retried on the next sync. The backend dedupes by event_id, so a retry after a lost response is safe.
- Events list each changed record by key (CDC keys, which the contract prefers) instead of a time window.
- Events with more than MAX_CHANGES_PER_EVENT changes are split into parts, all with the same window.

Publishing is off unless BACKEND_EVENTS_URL is set. Nothing here writes to HubSpot.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from knowledge_ingestion.config import config
from knowledge_ingestion.storage.base import KnowledgeStore, OutboxEvent

logger = logging.getLogger("knowledge_ingestion")

SCHEMA_VERSION = "backend.sync-event.v1"
PRODUCER = "knowledge-ingestion"
MAX_CHANGES_PER_EVENT = 1000
# sequence = run_id * SEQUENCE_STRIDE + part. Parts are numbered below the stride, so sequence stays
# strictly increasing across runs.
SEQUENCE_STRIDE = 1000
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# object type -> (The backend collection name, timestamp column, key column). Collection names are schema-qualified.
COLLECTIONS: dict[str, tuple[str, str | None, str]] = {
    "contacts": ("hubspot.contacts", "synced_at", "contact_id"),
    # pipeline_crm.prospect_accounts has no modification-time column, so changes must carry keys.
    "companies": ("pipeline_crm.prospect_accounts", None, "crm_account_id"),
}

Change = tuple[str, str, str]
"""(object_type, op, source_id). op is 'insert' for a new record and 'update' for a changed one."""


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _body(event: dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"), default=str)


def _envelope(
    *,
    event_id: str,
    event_type: str,
    status: str,
    sequence: int,
    window_start: datetime,
    window_end: datetime,
    data_version: str,
    collections: list[dict[str, Any]],
    published_at: datetime,
) -> dict[str, Any]:
    backend = config.backend
    return {
        "schema": SCHEMA_VERSION,
        "event_id": event_id,
        "event_type": event_type,
        "tenant_id": backend.tenant_id,
        "source_id": backend.source_id,
        "database_id": backend.database_id,
        "collections": collections,
        "window_start": _iso(window_start),
        "window_end": _iso(window_end),
        "data_version": data_version,
        "sequence": sequence,
        "status": status,
        "published_at": _iso(published_at),
        "producer": PRODUCER,
    }


def build_success_events(
    run_id: int,
    window_start: datetime,
    window_end: datetime,
    changes: Iterable[Change],
    published_at: datetime,
) -> list[OutboxEvent]:
    """Split the run's changes into completed events. Returns no events when nothing changed."""
    flat = list(changes)
    if not flat:
        return []

    chunks = [flat[i : i + MAX_CHANGES_PER_EVENT] for i in range(0, len(flat), MAX_CHANGES_PER_EVENT)]
    events: list[OutboxEvent] = []
    for part, chunk in enumerate(chunks):
        collections = []
        for object_type, (name, timestamp_column, key_column) in COLLECTIONS.items():
            part_changes = [
                {"op": op, "key": {key_column: source_id}}
                for obj, op, source_id in chunk
                if obj == object_type
            ]
            collections.append(
                {
                    "name": name,
                    "timestamp_column": timestamp_column,
                    "status": "completed",
                    "expected_changes": len(part_changes),
                    "changes": part_changes,
                }
            )
        event_id = f"hubspot-run{run_id}-part{part}"
        event = _envelope(
            event_id=event_id,
            event_type="database.sync.completed",
            status="completed",
            sequence=run_id * SEQUENCE_STRIDE + part,
            window_start=window_start,
            window_end=window_end,
            data_version=f"hubspot-run{run_id}",
            collections=collections,
            published_at=published_at,
        )
        events.append(OutboxEvent(event_id, window_start, window_end, _body(event)))
    return events


def build_failed_event(
    run_id: int,
    window_start: datetime,
    window_end: datetime,
    error: str,
    published_at: datetime,
) -> OutboxEvent:
    event_id = f"hubspot-run{run_id}-failed"
    event = _envelope(
        event_id=event_id,
        event_type="database.sync.failed",
        status="failed",
        sequence=run_id * SEQUENCE_STRIDE,
        window_start=window_start,
        window_end=window_end,
        data_version=f"hubspot-run{run_id}",
        collections=[],
        published_at=published_at,
    )
    event["error"] = error[:1000]
    return OutboxEvent(event_id, window_start, window_end, _body(event))


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver_pending(store: KnowledgeStore, source_system: str, limit: int = 50) -> int:
    """Send queued events in order. Returns how many the backend accepted. Stops at the first retryable failure."""
    backend = config.backend
    if not backend.enabled:
        return 0

    url = f"{backend.events_url}/events/sync"
    delivered = 0
    with httpx.Client(timeout=15) as client:
        for item in store.pending_outbox(source_system, limit):
            event_id = item["event_id"]
            body = item["body"].encode("utf-8")
            headers = {"Content-Type": "application/json", "X-Backend-Signature": _sign(body, backend.secret)}
            try:
                response = client.post(url, content=body, headers=headers)
            except httpx.HTTPError as error:
                store.mark_outbox_attempt(event_id, f"{type(error).__name__}: {error}", rejected=False)
                logger.warning("The backend is unreachable; %s stays queued", event_id)
                break

            if response.status_code in (200, 201, 202):  # 200 also covers "duplicate"
                store.mark_outbox_delivered(event_id)
                delivered += 1
                continue

            message = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code in (400, 422):
                # Backend says the event itself is invalid. Retrying the same body cannot succeed.
                store.mark_outbox_attempt(event_id, message, rejected=True)
                logger.error("The backend rejected %s permanently: %s", event_id, message)
                continue

            # 401/403 (secret mismatch), 404 (wrong URL), 5xx: keep the event queued and stop,
            # so later events are not sent ahead of it.
            store.mark_outbox_attempt(event_id, message, rejected=False)
            logger.warning("The backend returned %s for %s; will retry", response.status_code, event_id)
            break
    return delivered
