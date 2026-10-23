"""Durable runtime state in PostgreSQL (schema ``backend_runtime`` in the knowledge database).

* sync_events      -- every received event with processing status; claimed with a lease so a crashed worker's
                      event becomes claimable again (restart safety) and only one event per (tenant, source)
                      is processed at a time (ordering).
* watermarks       -- per collection: merged half-open intervals already investigated and the contiguous
                      watermark (end of the interval that starts at the first observed window).
* source_versions  -- the latest committed data/knowledge/schema version per (tenant, source) and sync status.
* investigations   -- deterministic (Stage A) summaries and optional agent (Stage B) results per event.
* answers          -- answers with the source-version manifest they were computed on, marked stale when a
                      later event changes a collection they read.
"""

from __future__ import annotations

import json
import socket
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.sync_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    database_id TEXT,
    event_type TEXT NOT NULL,
    sequence BIGINT,
    data_version TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL,               -- pending | processing | processed | deferred | failed | rejected
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_owner TEXT,
    lease_until TIMESTAMPTZ,
    last_error TEXT,
    notes JSONB NOT NULL DEFAULT '[]',
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sync_events_claim_idx ON backend_runtime.sync_events (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS sync_events_key_idx ON backend_runtime.sync_events (tenant_id, source_id, sequence);
CREATE TABLE IF NOT EXISTS backend_runtime.watermarks (
    tenant_id TEXT NOT NULL, source_id TEXT NOT NULL, collection TEXT NOT NULL,
    watermark TIMESTAMPTZ, intervals JSONB NOT NULL DEFAULT '[]', last_event_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_id, collection)
);
CREATE TABLE IF NOT EXISTS backend_runtime.source_versions (
    tenant_id TEXT NOT NULL, source_id TEXT NOT NULL,
    data_version TEXT, sequence BIGINT, knowledge_version TEXT, schema_version TEXT,
    last_sync_at TIMESTAMPTZ, last_window_end TIMESTAMPTZ, last_event_id TEXT,
    sync_status TEXT NOT NULL DEFAULT 'unknown', last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, source_id)
);
CREATE TABLE IF NOT EXISTS backend_runtime.investigations (
    investigation_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, tenant_id TEXT NOT NULL, source_id TEXT NOT NULL,
    status TEXT NOT NULL, summary JSONB NOT NULL, stage_b JSONB, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS investigations_scope_idx ON backend_runtime.investigations (tenant_id, source_id, created_at DESC);
CREATE TABLE IF NOT EXISTS backend_runtime.answers (
    answer_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, question TEXT NOT NULL, manifest JSONB NOT NULL,
    sources TEXT[] NOT NULL, collections TEXT[] NOT NULL, decision JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), stale BOOLEAN NOT NULL DEFAULT FALSE, stale_reason TEXT, stale_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS answers_scope_idx ON backend_runtime.answers (tenant_id, created_at DESC);
"""

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def _merge(intervals: List[List[str]]) -> List[List[str]]:
    iv = sorted([datetime.fromisoformat(a), datetime.fromisoformat(b)] for a, b in intervals)
    out: List[List[datetime]] = []
    for a, b in iv:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [[a.isoformat(), b.isoformat()] for a, b in out]


class RuntimeStore:
    def __init__(self, uri: str):
        self._pool = ThreadedConnectionPool(1, 8, uri, connect_timeout=5)
        self._exec(DDL)

    def _exec(self, sql: str, params: Any = None, fetch: str = "") -> Any:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = None
                if fetch == "one":
                    out = cur.fetchone()
                elif fetch == "all":
                    out = cur.fetchall()
                if fetch and cur.description:
                    cols = [d[0] for d in cur.description]
                    out = (dict(zip(cols, out)) if out else None) if fetch == "one" else [dict(zip(cols, r)) for r in out]
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    # ------------------------------------------------------------------ events
    def insert_event(self, ev: Dict[str, Any], status: str = "pending", error: Optional[str] = None) -> bool:
        """Insert once. Returns False when the event id was already seen (duplicate delivery)."""
        row = self._exec("""
            INSERT INTO backend_runtime.sync_events (event_id, tenant_id, source_id, database_id, event_type, sequence, data_version,
                window_start, window_end, payload, status, last_error)
            VALUES (%(event_id)s, %(tenant_id)s, %(source_id)s, %(database_id)s, %(event_type)s, %(sequence)s, %(data_version)s,
                %(window_start)s, %(window_end)s, %(payload)s, %(status)s, %(error)s)
            ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                         dict(ev, payload=Json(ev["payload"]), status=status, error=error), fetch="one")
        return row is not None

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.sync_events WHERE event_id = %s", (event_id,), fetch="one")

    def claim_next(self, lease_s: int = 300) -> Optional[Dict[str, Any]]:
        """Claim the oldest eligible event whose (tenant, source) has no other event in flight."""
        return self._exec("""
            UPDATE backend_runtime.sync_events e SET status = 'processing', attempts = e.attempts + 1, lease_owner = %s,
                   lease_until = now() + make_interval(secs => %s)
            WHERE e.event_id = (
                SELECT c.event_id FROM backend_runtime.sync_events c
                WHERE (c.status IN ('pending', 'deferred') AND c.next_attempt_at <= now())
                   OR (c.status = 'processing' AND c.lease_until < now())
                ORDER BY c.received_at, c.sequence NULLS LAST
                FOR UPDATE SKIP LOCKED LIMIT 1)
              AND NOT EXISTS (SELECT 1 FROM backend_runtime.sync_events p WHERE p.status = 'processing' AND p.lease_until >= now()
                              AND p.tenant_id = e.tenant_id AND p.source_id = e.source_id AND p.event_id <> e.event_id)
            RETURNING e.*""", (WORKER_ID, lease_s), fetch="one")

    def finish_event(self, event_id: str, status: str, error: Optional[str] = None, notes: Optional[List[str]] = None,
                     retry_in_s: Optional[float] = None) -> None:
        self._exec("""
            UPDATE backend_runtime.sync_events SET status = %s, last_error = %s, lease_owner = NULL, lease_until = NULL,
                   notes = notes || %s::jsonb, finished_at = CASE WHEN %s IN ('processed', 'failed', 'rejected') THEN now() END,
                   next_attempt_at = now() + make_interval(secs => %s)
            WHERE event_id = %s""", (status, error, json.dumps(notes or []), status, retry_in_s or 0, event_id))

    def events(self, tenant_id: str, source_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        sql = ("SELECT event_id, source_id, event_type, data_version, sequence, window_start, window_end, status, attempts, "
               "last_error, notes, received_at, finished_at, payload FROM backend_runtime.sync_events WHERE tenant_id = %s")
        params: List[Any] = [tenant_id]
        if source_id:
            sql += " AND source_id = %s"
            params.append(source_id)
        return self._exec(sql + " ORDER BY received_at DESC LIMIT %s", params + [limit], fetch="all")

    # ------------------------------------------------------------------ watermarks
    def watermark(self, tenant: str, source: str, collection: str) -> Optional[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.watermarks WHERE tenant_id = %s AND source_id = %s AND collection = %s",
                          (tenant, source, collection), fetch="one")

    def cover(self, tenant: str, source: str, collection: str, start: datetime, end: datetime, event_id: str) -> Dict[str, Any]:
        """Record [start, end) as investigated; returns contiguous watermark and remaining gaps."""
        cur = self.watermark(tenant, source, collection)
        intervals = _merge((cur["intervals"] if cur else []) + [[start.isoformat(), end.isoformat()]])
        watermark = intervals[0][1]
        gaps = [[intervals[i][1], intervals[i + 1][0]] for i in range(len(intervals) - 1)]
        self._exec("""
            INSERT INTO backend_runtime.watermarks (tenant_id, source_id, collection, watermark, intervals, last_event_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, source_id, collection) DO UPDATE SET watermark = EXCLUDED.watermark,
                intervals = EXCLUDED.intervals, last_event_id = EXCLUDED.last_event_id, updated_at = now()""",
                   (tenant, source, collection, watermark, Json(intervals), event_id))
        return {"watermark": watermark, "intervals": intervals, "gaps": gaps}

    # ------------------------------------------------------------------ source versions
    def source_version(self, tenant: str, source: str) -> Optional[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.source_versions WHERE tenant_id = %s AND source_id = %s",
                          (tenant, source), fetch="one")

    def source_versions(self, tenant: str) -> List[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.source_versions WHERE tenant_id = %s ORDER BY source_id", (tenant,), fetch="all")

    def advance_version(self, tenant: str, source: str, data_version: str, sequence: Optional[int], event_id: str,
                        window_end: datetime, schema_version: Optional[str], knowledge_version: Optional[str]) -> bool:
        """Advance only forward (never regress to an older sequence). Returns True if the version moved."""
        row = self._exec("""
            INSERT INTO backend_runtime.source_versions (tenant_id, source_id, data_version, sequence, knowledge_version, schema_version,
                last_sync_at, last_window_end, last_event_id, sync_status, last_error, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now(), %s, %s, 'ok', NULL, now())
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                data_version = EXCLUDED.data_version, sequence = EXCLUDED.sequence,
                knowledge_version = COALESCE(EXCLUDED.knowledge_version, backend_runtime.source_versions.knowledge_version),
                schema_version = COALESCE(EXCLUDED.schema_version, backend_runtime.source_versions.schema_version),
                last_sync_at = now(), last_window_end = GREATEST(EXCLUDED.last_window_end, backend_runtime.source_versions.last_window_end),
                last_event_id = EXCLUDED.last_event_id, sync_status = 'ok', last_error = NULL, updated_at = now()
            WHERE backend_runtime.source_versions.sequence IS NULL OR EXCLUDED.sequence IS NULL
               OR EXCLUDED.sequence > backend_runtime.source_versions.sequence
            RETURNING source_id""", (tenant, source, data_version, sequence, knowledge_version, schema_version, window_end, event_id),
                         fetch="one")
        return row is not None

    def mark_sync_status(self, tenant: str, source: str, status: str, error: Optional[str] = None) -> None:
        self._exec("""
            INSERT INTO backend_runtime.source_versions (tenant_id, source_id, sync_status, last_error, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET sync_status = EXCLUDED.sync_status, last_error = EXCLUDED.last_error,
                updated_at = now()""", (tenant, source, status, error))

    # ------------------------------------------------------------------ investigations / answers
    def record_investigation(self, event_id: str, tenant: str, source: str, status: str, summary: Dict[str, Any],
                             stage_b: Optional[Dict[str, Any]] = None) -> str:
        iid = f"inv-{uuid.uuid4().hex[:12]}"
        self._exec("""INSERT INTO backend_runtime.investigations (investigation_id, event_id, tenant_id, source_id, status, summary, stage_b)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (iid, event_id, tenant, source, status, Json(summary, dumps=lambda o: json.dumps(o, default=str)),
                    Json(stage_b, dumps=lambda o: json.dumps(o, default=str)) if stage_b else None))
        return iid

    def investigations(self, tenant: str, source: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM backend_runtime.investigations WHERE tenant_id = %s"
        params: List[Any] = [tenant]
        if source:
            sql += " AND source_id = %s"
            params.append(source)
        return self._exec(sql + " ORDER BY created_at DESC LIMIT %s", params + [limit], fetch="all")

    def save_answer(self, tenant: str, question: str, manifest: Dict[str, Any], sources: Sequence[str],
                    collections: Sequence[str], decision: Dict[str, Any]) -> str:
        aid = f"ans-{uuid.uuid4().hex[:12]}"
        self._exec("""INSERT INTO backend_runtime.answers (answer_id, tenant_id, question, manifest, sources, collections, decision)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                   (aid, tenant, question, Json(manifest, dumps=lambda o: json.dumps(o, default=str)), list(sources),
                    list(collections), Json(decision, dumps=lambda o: json.dumps(o, default=str))))
        return aid

    def mark_answers_stale(self, tenant: str, source: str, collections: Sequence[str], reason: str) -> int:
        rows = self._exec("""
            UPDATE backend_runtime.answers SET stale = TRUE, stale_reason = %s, stale_at = now()
            WHERE tenant_id = %s AND NOT stale AND %s = ANY(sources) AND collections && %s::text[]
            RETURNING answer_id""", (reason, tenant, source, list(collections)), fetch="all")
        return len(rows or [])

    def answers(self, tenant: str, limit: int = 30) -> List[Dict[str, Any]]:
        return self._exec("SELECT answer_id, question, manifest, sources, collections, created_at, stale, stale_reason, stale_at, "
                          "decision->>'final_answer' AS final_answer FROM backend_runtime.answers WHERE tenant_id = %s "
                          "ORDER BY created_at DESC LIMIT %s", (tenant, limit), fetch="all")

    def reset_tenant(self, tenant: str) -> None:
        for t in ("sync_events", "watermarks", "source_versions", "investigations", "answers"):
            self._exec(f"DELETE FROM backend_runtime.{t} WHERE tenant_id = %s", (tenant,))
