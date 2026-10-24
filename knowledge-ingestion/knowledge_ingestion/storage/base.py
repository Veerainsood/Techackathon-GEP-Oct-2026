"""Storage interface for the shared knowledge layer.

The pipeline and the API depend only on this interface. A database is supported
by its SQLAlchemy driver being listed in config.SUPPORTED_DRIVERS. The tables and
queries are shared, so no other code changes are needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

RecordBatch = dict[str, list[tuple[str, dict[str, Any]]]]
"""Records grouped by object type. Each record is (source_id, properties)."""


@dataclass(frozen=True)
class OutboxEvent:
    """A sync event waiting to be delivered to the backend. It is written in the same transaction as the run."""

    event_id: str
    window_start: datetime
    window_end: datetime
    body: str
    """The exact JSON body that will be sent. The signature is computed over these bytes at send time."""


class KnowledgeStore(Protocol):
    def init_schema(self) -> None:
        """Create the tables if they don't exist. Safe to call more than once."""

    def start_run(self, source_system: str) -> int:
        """Record that an ingestion run (one collection) has started, and return its ID."""

    def save_snapshot(
        self,
        run_id: int,
        source_system: str,
        fetched_at: datetime,
        records: RecordBatch,
        cursors: dict[str, int] | None = None,
        outbox: list[OutboxEvent] | None = None,
    ) -> None:
        """Append the records, advance the cursors, mark the run as succeeded, and queue the outbox events, in one atomic write.

        cursors maps object type to the newest modified time seen, in epoch milliseconds.
        """

    def fail_run(self, run_id: int, error: str, outbox: list[OutboxEvent] | None = None) -> None:
        """Mark the run as failed, store the error message, and queue any outbox events in the same write."""

    def last_outbox_window_end(self, source_system: str) -> datetime | None:
        """Return the end of the newest queued event window for the source, or None if no event was ever queued."""

    def pending_outbox(self, source_system: str, limit: int) -> list[dict[str, Any]]:
        """Return queued events not yet delivered or rejected, oldest first. Each item has event_id and body."""

    def mark_outbox_delivered(self, event_id: str) -> None:
        """Record that the backend accepted the event (or already had it)."""

    def mark_outbox_attempt(self, event_id: str, error: str, rejected: bool) -> None:
        """Record a failed delivery attempt. rejected=True means the backend refused the event permanently."""

    def get_cursor(self, source_system: str, object_type: str) -> int | None:
        """Return the stored cursor (epoch milliseconds), or None if the object type has never been polled."""

    def list_runs(self, source_system: str, limit: int) -> list[dict[str, Any]]:
        """Return the most recent runs, newest first."""

    def latest_properties(self, source_system: str, object_type: str, source_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Return the properties of the newest stored snapshot for each of the given source IDs."""

    def latest_records(self, source_system: str, object_type: str, limit: int) -> list[dict[str, Any]]:
        """Return the newest snapshot of each record, most recently fetched first."""
