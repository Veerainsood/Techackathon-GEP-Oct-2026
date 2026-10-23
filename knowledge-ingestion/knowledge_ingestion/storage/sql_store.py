"""Shared SQL adapter for the knowledge layer, built on SQLAlchemy Core.

Tables and queries are written once here. SQLAlchemy generates the SQL for the
database selected by DATABASE_URL, for example PostgreSQL, Azure SQL / SQL Server,
or SQLite. To support another database, install its SQLAlchemy driver and add
its driver name to SUPPORTED_DRIVERS in config.py.

Conventions:
- Timestamps are stored as naive UTC. Drivers handle timezone-aware types
  inconsistently, so conversion happens in Python.
- Payloads are stored as JSON text, which works on every database.
- raw_records is append-only. Every poll adds snapshots and never updates or
  deletes earlier rows. Readers take the newest snapshot per source record.
- sync_state holds one cursor per object type: the newest modified time polled so far.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Unicode,
    UnicodeText,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.engine import Engine, make_url

from knowledge_ingestion.storage.base import OutboxEvent, RecordBatch

metadata = MetaData()

# SQLite only auto-increments an INTEGER primary key, so it gets a different type.
PRIMARY_KEY_TYPE = BigInteger().with_variant(Integer(), "sqlite")
# DATETIME2 keeps microseconds on SQL Server. DATETIME rounds them to milliseconds.
TIMESTAMP_TYPE = DateTime().with_variant(mssql.DATETIME2(6), "mssql")

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("id", PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True),
    Column("source_system", Unicode(100), nullable=False),
    Column("started_at", TIMESTAMP_TYPE, nullable=False),
    Column("finished_at", TIMESTAMP_TYPE, nullable=True),
    Column("status", Unicode(20), nullable=False),
    Column("record_counts", UnicodeText, nullable=True),
    Column("error", UnicodeText, nullable=True),
)

raw_records = Table(
    "raw_records",
    metadata,
    Column("id", PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True),
    Column("run_id", BigInteger, ForeignKey("ingestion_runs.id"), nullable=False),
    Column("source_system", Unicode(100), nullable=False),
    Column("object_type", Unicode(100), nullable=False),
    Column("source_id", Unicode(200), nullable=False),
    Column("payload", UnicodeText, nullable=False),
    Column("fetched_at", TIMESTAMP_TYPE, nullable=False),
    Index("raw_records_lookup", "source_system", "object_type", "source_id", "fetched_at"),
)

sync_state = Table(
    "sync_state",
    metadata,
    Column("source_system", Unicode(100), primary_key=True),
    Column("object_type", Unicode(100), primary_key=True),
    Column("cursor_ms", BigInteger, nullable=False),
    Column("updated_at", TIMESTAMP_TYPE, nullable=False),
)


udre_outbox = Table(
    "udre_outbox",
    metadata,
    Column("id", PRIMARY_KEY_TYPE, primary_key=True, autoincrement=True),
    Column("event_id", Unicode(200), nullable=False, unique=True),
    Column("source_system", Unicode(100), nullable=False),
    Column("window_start", TIMESTAMP_TYPE, nullable=False),
    Column("window_end", TIMESTAMP_TYPE, nullable=False),
    Column("body", UnicodeText, nullable=False),
    Column("created_at", TIMESTAMP_TYPE, nullable=False),
    Column("delivered_at", TIMESTAMP_TYPE, nullable=True),
    Column("rejected_at", TIMESTAMP_TYPE, nullable=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", UnicodeText, nullable=True),
    Index("udre_outbox_pending", "source_system", "delivered_at", "rejected_at"),
)


def _utc_naive(value: datetime) -> datetime:
    """Convert to naive UTC. A naive value is assumed to already be UTC."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None else None


def create_db_engine(database_url: str) -> Engine:
    options: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "mssql":
        # Sends bulk inserts in one round trip. Only the pyodbc driver for SQL Server accepts this option.
        options["fast_executemany"] = True
    return create_engine(database_url, **options)


def _insert_outbox(conn: Any, source_system: str, events: list[OutboxEvent]) -> None:
    """Queue events inside the caller's transaction, so they are committed only with the run."""
    if not events:
        return
    now = _utcnow()
    conn.execute(
        insert(udre_outbox),
        [
            {
                "event_id": event.event_id,
                "source_system": source_system,
                "window_start": _utc_naive(event.window_start),
                "window_end": _utc_naive(event.window_end),
                "body": event.body,
                "created_at": now,
                "attempts": 0,
            }
            for event in events
        ],
    )


class SqlStore:
    def __init__(self, database_url: str) -> None:
        self._engine = create_db_engine(database_url)

    def init_schema(self) -> None:
        # Creates only the tables that don't exist yet, so it's safe to call on every start.
        metadata.create_all(self._engine)

    def start_run(self, source_system: str) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(ingestion_runs).values(
                    source_system=source_system,
                    started_at=_utcnow(),
                    status="running",
                )
            )
            return int(result.inserted_primary_key[0])

    def save_snapshot(
        self,
        run_id: int,
        source_system: str,
        fetched_at: datetime,
        records: RecordBatch,
        cursors: dict[str, int] | None = None,
        outbox: list[OutboxEvent] | None = None,
    ) -> None:
        fetched_utc = _utc_naive(fetched_at)
        rows = [
            {
                "run_id": run_id,
                "source_system": source_system,
                "object_type": object_type,
                "source_id": source_id,
                "payload": json.dumps(payload, default=str),
                "fetched_at": fetched_utc,
            }
            for object_type, items in records.items()
            for source_id, payload in items
        ]
        counts = {object_type: len(items) for object_type, items in records.items()}

        # One transaction: records, cursors, and the succeeded status are written together, or none are.
        with self._engine.begin() as conn:
            if rows:  # some databases reject an empty bulk insert
                conn.execute(insert(raw_records), rows)
            for object_type, cursor_ms in (cursors or {}).items():
                conn.execute(
                    delete(sync_state).where(
                        sync_state.c.source_system == source_system,
                        sync_state.c.object_type == object_type,
                    )
                )
                conn.execute(
                    insert(sync_state).values(
                        source_system=source_system,
                        object_type=object_type,
                        cursor_ms=cursor_ms,
                        updated_at=_utcnow(),
                    )
                )
            conn.execute(
                update(ingestion_runs)
                .where(ingestion_runs.c.id == run_id)
                .values(status="succeeded", finished_at=_utcnow(), record_counts=json.dumps(counts))
            )
            _insert_outbox(conn, source_system, outbox or [])

    def fail_run(self, run_id: int, error: str, outbox: list[OutboxEvent] | None = None) -> None:
        source_system = self._source_of_run(run_id)
        with self._engine.begin() as conn:
            conn.execute(
                update(ingestion_runs)
                .where(ingestion_runs.c.id == run_id)
                .values(status="failed", finished_at=_utcnow(), error=error)
            )
            _insert_outbox(conn, source_system, outbox or [])

    def _source_of_run(self, run_id: int) -> str:
        with self._engine.connect() as conn:
            return conn.execute(select(ingestion_runs.c.source_system).where(ingestion_runs.c.id == run_id)).scalar_one()

    def last_outbox_window_end(self, source_system: str) -> datetime | None:
        with self._engine.connect() as conn:
            value = conn.execute(
                select(func.max(udre_outbox.c.window_end)).where(udre_outbox.c.source_system == source_system)
            ).scalar()
        return _as_utc_aware(value)

    def pending_outbox(self, source_system: str, limit: int) -> list[dict[str, Any]]:
        stmt = (
            select(udre_outbox.c.event_id, udre_outbox.c.body)
            .where(
                udre_outbox.c.source_system == source_system,
                udre_outbox.c.delivered_at.is_(None),
                udre_outbox.c.rejected_at.is_(None),
            )
            .order_by(udre_outbox.c.id)
            .limit(limit)
        )
        with self._engine.connect() as conn:
            return [{"event_id": row.event_id, "body": row.body} for row in conn.execute(stmt).all()]

    def mark_outbox_delivered(self, event_id: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(udre_outbox)
                .where(udre_outbox.c.event_id == event_id)
                .values(delivered_at=_utcnow(), attempts=udre_outbox.c.attempts + 1, last_error=None)
            )

    def mark_outbox_attempt(self, event_id: str, error: str, rejected: bool) -> None:
        values: dict[str, Any] = {"attempts": udre_outbox.c.attempts + 1, "last_error": error[:4000]}
        if rejected:
            values["rejected_at"] = _utcnow()
        with self._engine.begin() as conn:
            conn.execute(update(udre_outbox).where(udre_outbox.c.event_id == event_id).values(**values))

    def get_cursor(self, source_system: str, object_type: str) -> int | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(sync_state.c.cursor_ms).where(
                    sync_state.c.source_system == source_system,
                    sync_state.c.object_type == object_type,
                )
            ).first()
        return int(row[0]) if row is not None else None

    def list_runs(self, source_system: str, limit: int) -> list[dict[str, Any]]:
        stmt = (
            select(
                ingestion_runs.c.id,
                ingestion_runs.c.status,
                ingestion_runs.c.started_at,
                ingestion_runs.c.finished_at,
                ingestion_runs.c.record_counts,
                ingestion_runs.c.error,
            )
            .where(ingestion_runs.c.source_system == source_system)
            .order_by(ingestion_runs.c.id.desc())
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()

        return [
            {
                "run_id": int(row.id),
                "status": row.status,
                "started_at": _as_utc_aware(row.started_at),
                "finished_at": _as_utc_aware(row.finished_at),
                "counts": json.loads(row.record_counts) if row.record_counts else None,
                "error": row.error,
            }
            for row in rows
        ]

    def latest_properties(self, source_system: str, object_type: str, source_ids: list[str]) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        # Chunked, because some databases limit the number of parameters in one query.
        chunk_size = 500
        for start in range(0, len(source_ids), chunk_size):
            chunk = source_ids[start : start + chunk_size]
            ranked = (
                select(
                    raw_records.c.source_id,
                    raw_records.c.payload,
                    func.row_number()
                    .over(
                        partition_by=raw_records.c.source_id,
                        order_by=(raw_records.c.fetched_at.desc(), raw_records.c.id.desc()),
                    )
                    .label("rn"),
                )
                .where(
                    raw_records.c.source_system == source_system,
                    raw_records.c.object_type == object_type,
                    raw_records.c.source_id.in_(chunk),
                )
                .subquery("ranked")
            )
            stmt = select(ranked.c.source_id, ranked.c.payload).where(ranked.c.rn == 1)
            with self._engine.connect() as conn:
                for source_id, payload in conn.execute(stmt).all():
                    found[source_id] = json.loads(payload)
        return found

    def latest_records(self, source_system: str, object_type: str, limit: int) -> list[dict[str, Any]]:
        ranked = (
            select(
                raw_records.c.source_id,
                raw_records.c.payload,
                raw_records.c.fetched_at,
                raw_records.c.run_id,
                func.row_number()
                .over(
                    partition_by=raw_records.c.source_id,
                    order_by=(raw_records.c.fetched_at.desc(), raw_records.c.id.desc()),
                )
                .label("rn"),
            )
            .where(
                raw_records.c.source_system == source_system,
                raw_records.c.object_type == object_type,
            )
            .subquery("ranked")
        )
        stmt = (
            select(ranked.c.source_id, ranked.c.payload, ranked.c.fetched_at, ranked.c.run_id)
            .where(ranked.c.rn == 1)
            .order_by(ranked.c.fetched_at.desc(), ranked.c.source_id)
            .limit(limit)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).all()

        # Timestamps are stored as naive UTC, so the UTC offset is attached when reading.
        return [
            {
                "id": source_id,
                "properties": json.loads(payload),
                "fetched_at": fetched_at.replace(tzinfo=timezone.utc),
                "run_id": int(run_id),
            }
            for source_id, payload, fetched_at, run_id in rows
        ]
