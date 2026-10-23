"""Poll HubSpot for records changed since the last run (read-only), and store them as raw snapshots.

Each poll is one ingestion run, reported as a named collection with start and end times.
For each object type, the cursor is the newest modified time seen so far. The next poll
reads from the cursor minus a short overlap, so records HubSpot indexes late are not missed.
Overlapping records are stored again as new snapshots, which is expected.

When BACKEND_EVENTS_URL is set, each run that changed something also queues a backend.sync-event.v1
event in the same transaction, and the queued events are delivered to the backend after the poll.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from knowledge_ingestion.config import config
from knowledge_ingestion.connectors.hubspot.client import HubSpotClient, HubSpotRecord
from knowledge_ingestion.connectors.hubspot.properties import MODIFIED_PROPERTY, OBJECT_PROPERTIES
from knowledge_ingestion.publish.backend_events import (
    EPOCH,
    Change,
    build_failed_event,
    build_success_events,
    deliver_pending,
)
from knowledge_ingestion.storage.base import KnowledgeStore, OutboxEvent, RecordBatch
from knowledge_ingestion.storage.factory import get_store
from knowledge_ingestion.storage.warehouse import get_warehouse

logger = logging.getLogger("knowledge_ingestion")

SOURCE_SYSTEM = "hubspot"
OVERLAP_MS = 2 * 60 * 1000  # re-read two minutes before the cursor

# One poll at a time, so the scheduler and a manual sync cannot overlap.
_poll_lock = threading.Lock()


@dataclass
class SyncResult:
    run_id: int
    collection_name: str
    started_at: datetime
    finished_at: datetime
    records: dict[str, list[HubSpotRecord]]


def collection_name(started_at: datetime) -> str:
    return f"{SOURCE_SYSTEM}_{started_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"


def _to_epoch_ms(value: Any) -> int | None:
    """HubSpot returns date-times as ISO 8601 strings, or as epoch milliseconds for some properties."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _newest_modified_ms(records: list[HubSpotRecord], modified_property: str) -> int | None:
    values = [_to_epoch_ms(record.properties.get(modified_property)) for record in records]
    known = [value for value in values if value is not None]
    return max(known) if known else None


def sync_hubspot(store: KnowledgeStore | None = None) -> SyncResult:
    with _poll_lock:
        store = store or get_store()
        try:
            return _poll_once(store)
        finally:
            _deliver_safely(store)


def _deliver_safely(store: KnowledgeStore) -> None:
    """Send queued events to the backend. A failure is logged and kept in the queue; it never changes the sync result."""
    try:
        deliver_pending(store, SOURCE_SYSTEM)
    except Exception:
        logger.exception("Delivering sync events to the backend failed; they stay queued")


def _log_batch(
    batch_id: str,
    started_at: datetime,
    status: str,
    extracted: int,
    loaded: int,
    error: str | None,
    finished_at: datetime | None = None,
) -> None:
    """Write the run to the warehouse log. A failure here is logged and does not change the sync result."""
    try:
        get_warehouse().log_batch(
            batch_id, started_at, finished_at or datetime.now(timezone.utc), status, extracted, loaded, error
        )
    except Exception:
        logger.exception("Could not write the warehouse batch log for %s", batch_id)


def _failure_events(run_id: int, window_start: datetime, window_end: datetime, error: str) -> list[OutboxEvent]:
    if not config.backend.enabled:
        return []
    return [build_failed_event(run_id, window_start, window_end, error, datetime.now(timezone.utc))]


def _poll_once(store: KnowledgeStore) -> SyncResult:
    started_at = datetime.now(timezone.utc)
    batch_id = collection_name(started_at)
    run_id = store.start_run(SOURCE_SYSTEM)
    # Events cover consecutive windows: each starts where the last queued event ended.
    window_start = (store.last_outbox_window_end(SOURCE_SYSTEM) if config.backend.enabled else None) or EPOCH

    records: dict[str, list[HubSpotRecord]] = {}
    new_cursors: dict[str, int] = {}
    changes: list[Change] = []
    try:
        with HubSpotClient() as client:
            for object_type, properties in OBJECT_PROPERTIES.items():
                modified_property = MODIFIED_PROPERTY[object_type]
                cursor = store.get_cursor(SOURCE_SYSTEM, object_type)
                # No cursor yet means the first run, which reads everything (epoch 0).
                since_ms = max(0, (cursor or 0) - OVERLAP_MS)

                fetched = client.search_modified_since(object_type, properties, modified_property, since_ms)

                # The overlap re-reads recent records. Store only versions not already stored.
                known = store.latest_properties(SOURCE_SYSTEM, object_type, [r.id for r in fetched])
                records[object_type] = [
                    record
                    for record in fetched
                    if _to_epoch_ms(record.properties.get(modified_property)) is None
                    or _to_epoch_ms(known.get(record.id, {}).get(modified_property))
                    != _to_epoch_ms(record.properties.get(modified_property))
                ]
                changes.extend(
                    (object_type, "update" if record.id in known else "insert", record.id)
                    for record in records[object_type]
                )

                # The cursor advances based on everything read, including skipped records.
                newest = _newest_modified_ms(fetched, modified_property)
                if newest is not None and (cursor is None or newest > cursor):
                    new_cursors[object_type] = newest
    except Exception as error:
        store.fail_run(run_id, str(error), outbox=_failure_events(run_id, window_start, started_at, str(error)))
        _log_batch(batch_id, started_at, "FAILED", 0, 0, str(error))
        raise

    batch: RecordBatch = {
        object_type: [(item.id, item.properties) for item in items]
        for object_type, items in records.items()
    }
    extracted = sum(len(items) for items in records.values())
    try:
        # Warehouse first, then our raw snapshot. Both are upserts or appends, so a retry is safe.
        account_changes = get_warehouse().load(batch_id, records, started_at)
        outbox: list[OutboxEvent] = []
        if config.backend.enabled:
            # Contacts are keyed by their HubSpot id. Companies are reported as the warehouse accounts the load
            # created or changed (linked companies keep their existing CRM account id, not the HubSpot id).
            event_changes = [change for change in changes if change[0] == "contacts"] + [
                ("companies", op, account_id) for account_id, op in sorted(account_changes.items())
            ]
            outbox = build_success_events(run_id, window_start, started_at, event_changes, datetime.now(timezone.utc))
        store.save_snapshot(run_id, SOURCE_SYSTEM, started_at, batch, cursors=new_cursors, outbox=outbox)
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        store.fail_run(run_id, message, outbox=_failure_events(run_id, window_start, started_at, message))
        _log_batch(batch_id, started_at, "FAILED", extracted, 0, message)
        raise

    finished_at = datetime.now(timezone.utc)
    _log_batch(batch_id, started_at, "SUCCEEDED", extracted, extracted, None, finished_at)
    return SyncResult(
        run_id=run_id,
        collection_name=batch_id,
        started_at=started_at,
        finished_at=finished_at,
        records=records,
    )
