"""Local analytical snapshot (DuckDB) for API sources that cannot run arbitrary SQL.

Each API object becomes a table.  Records are normalized (nested JSON flattened with ``__``, arrays kept
as JSON text, snake_case column names), upserted by their stable source id, and stamped with
``_backend_synced_at``.  Tombstones are kept: ``_backend_archived`` mirrors the source's archived/deleted
flag and ``_backend_present`` turns false when a full refresh no longer returns a record.  Data and the
per-object checkpoint are committed in one transaction, so a failed object never advances its checkpoint.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb
import pandas as pd

from backend.contracts.models import CollectionSchema, ColumnSchema, SourceCapabilities, SourceDescriptor, SourceType
from backend.gateway.base import SQLSourceAdapter
from backend.ingestion.base import ObjectSpec, RelationshipHint, snake

META_TABLES = ("_backend_sync_state", "_backend_field_meta", "_backend_relationships", "_backend_change_queue")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_records(records: Sequence[Dict[str, Any]], pk: str) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Flatten nested JSON. Returns the frame and a map normalized_column -> original JSON path."""
    if not records:
        return pd.DataFrame(), {}
    flat = pd.json_normalize(list(records), sep="__", max_level=4)
    mapping: Dict[str, str] = {}
    cols = []
    for c in flat.columns:
        n = "__".join(snake(part) for part in c.split("__"))
        while n in mapping:
            n += "_"
        mapping[n] = c.replace("__", ".")
        cols.append(n)
    flat.columns = cols
    for c in flat.columns:
        if flat[c].map(lambda v: isinstance(v, (list, dict))).any():
            flat[c] = flat[c].map(lambda v: json.dumps(v, default=str) if isinstance(v, (list, dict)) else v)
    pkn = snake(pk)
    if pkn in flat.columns:
        flat[pkn] = flat[pkn].astype(str)
    return flat, mapping


class SnapshotStore:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = duckdb.connect(path)
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS _backend_sync_state (object VARCHAR PRIMARY KEY, mode VARCHAR,
                checkpoint VARCHAR, last_sync VARCHAR, last_full_refresh VARCHAR, rows_upserted BIGINT, rows_total BIGINT,
                status VARCHAR, error VARCHAR)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS _backend_field_meta (object VARCHAR, column_name VARCHAR,
                source_path VARCHAR, label VARCHAR, data_type VARCHAR, description VARCHAR, options VARCHAR,
                PRIMARY KEY (object, column_name))""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS _backend_relationships (child VARCHAR, child_col VARCHAR,
                parent VARCHAR, parent_col VARCHAR, properties VARCHAR)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS _backend_change_queue (object VARCHAR, object_id VARCHAR,
                event_type VARCHAR, occurred_at VARCHAR, processed BOOLEAN DEFAULT FALSE)""")

    # ------------------------------------------------------------------ state
    def state(self, obj: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            row = self.conn.execute("SELECT * FROM _backend_sync_state WHERE object = ?", [obj]).fetchone()
            if not row:
                return None
            cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, row))

    def all_states(self) -> List[Dict[str, Any]]:
        with self.lock:
            cur = self.conn.execute("SELECT * FROM _backend_sync_state ORDER BY object")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def record_failure(self, obj: str, error: str) -> None:
        with self.lock:
            prev = self.state(obj)
            self.conn.execute("INSERT OR REPLACE INTO _backend_sync_state VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?)",
                              [obj, prev["mode"] if prev else None, prev["checkpoint"] if prev else None,
                               prev["last_sync"] if prev else None, prev["last_full_refresh"] if prev else None,
                               0, prev["rows_total"] if prev else 0, error[:500]])

    def tables(self) -> List[str]:
        with self.lock:
            rows = self.conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
        return sorted(r[0] for r in rows if r[0] not in META_TABLES)

    # ------------------------------------------------------------------ writes
    def _ensure_columns(self, table: str, df: pd.DataFrame) -> None:
        existing = {r[0]: r[1] for r in self.conn.execute(f'DESCRIBE "{table}"').fetchall()}
        for c in df.columns:
            if c not in existing:
                self.conn.register("__probe", df[[c]].head(0))
                dtype = self.conn.execute('DESCRIBE "__probe"').fetchall()[0][1]
                self.conn.unregister("__probe")
                self.conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" {dtype}')

    def upsert(self, spec: ObjectSpec, records: List[Dict[str, Any]], mode: str, checkpoint: Optional[str],
               archived_ids: Sequence[str] = (), field_meta: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, int]:
        """Upsert records and advance the checkpoint atomically."""
        table, pk = spec.name, snake(spec.primary_key)
        df, mapping = normalize_records(records, spec.primary_key)
        ts = now_iso()
        with self.lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                exists = table in self.tables()
                if not df.empty:
                    df["_backend_synced_at"] = ts
                    df["_backend_present"] = True
                    if "_backend_archived" not in df.columns:
                        df["_backend_archived"] = df[pk].isin(set(map(str, archived_ids)))
                    self.conn.register("__batch", df)
                    if not exists:
                        self.conn.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "__batch" WHERE 1=0')
                        exists = True
                    self._ensure_columns(table, df)
                    self.conn.execute(f'DELETE FROM "{table}" WHERE "{pk}" IN (SELECT "{pk}" FROM "__batch")')
                    self.conn.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM "__batch"')
                    self.conn.unregister("__batch")
                if exists and archived_ids:
                    ids = list(map(str, archived_ids))
                    self.conn.execute(f'UPDATE "{table}" SET "_backend_archived" = TRUE WHERE list_contains(?::VARCHAR[], "{pk}")', [ids])
                if exists and mode == "full" and not df.empty:
                    seen = df[pk].tolist() + list(map(str, archived_ids))
                    self.conn.execute(f'UPDATE "{table}" SET "_backend_present" = FALSE WHERE NOT list_contains(?::VARCHAR[], "{pk}")', [seen])
                for col, path in mapping.items():
                    meta = (field_meta or {}).get(col) or (field_meta or {}).get(path) or {}
                    self.conn.execute("INSERT OR REPLACE INTO _backend_field_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                                      [table, col, path, meta.get("label"), meta.get("type"), meta.get("description"),
                                       json.dumps(meta.get("options")) if meta.get("options") else None])
                total = self.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if exists else 0
                prev = self.state(table)
                self.conn.execute("INSERT OR REPLACE INTO _backend_sync_state VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', NULL)",
                                  [table, mode, checkpoint, ts, ts if mode == "full" else (prev or {}).get("last_full_refresh"),
                                   len(df), total])
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return {"upserted": len(df), "archived": len(archived_ids), "total": total}

    def replace_table(self, table: str, rows: List[Dict[str, Any]], pk: Optional[str] = None) -> int:
        """Derived/metadata tables (associations, pipelines) are rebuilt as a whole each sync."""
        df, _ = normalize_records(rows, pk or "id")
        with self.lock:
            self.conn.execute("BEGIN TRANSACTION")
            try:
                self.conn.execute(f'DROP TABLE IF EXISTS "{table}"')
                if not df.empty:
                    df["_backend_synced_at"] = now_iso()
                    self.conn.register("__batch", df)
                    self.conn.execute(f'CREATE TABLE "{table}" AS SELECT * FROM "__batch"')
                    self.conn.unregister("__batch")
                self.conn.execute("INSERT OR REPLACE INTO _backend_sync_state VALUES (?, 'full', NULL, ?, ?, ?, ?, 'ok', NULL)",
                                  [table, now_iso(), now_iso(), len(df), len(df)])
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return len(df)

    def set_relationships(self, hints: Sequence[RelationshipHint]) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM _backend_relationships")
            for h in hints:
                self.conn.execute("INSERT INTO _backend_relationships VALUES (?, ?, ?, ?, ?)",
                                  [h.child, h.child_col, h.parent, h.parent_col, json.dumps(h.properties)])

    def enqueue_change(self, obj: str, object_id: str, event_type: str, occurred_at: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO _backend_change_queue VALUES (?, ?, ?, ?, FALSE)", [obj, str(object_id), event_type, occurred_at])

    def pending_changes(self, obj: str) -> List[Dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT object_id, event_type FROM _backend_change_queue WHERE object = ? AND NOT processed",
                                     [obj]).fetchall()
        return [{"object_id": r[0], "event_type": r[1]} for r in rows]

    def mark_changes_processed(self, obj: str) -> None:
        with self.lock:
            self.conn.execute("UPDATE _backend_change_queue SET processed = TRUE WHERE object = ?", [obj])


class SnapshotSourceAdapter(SQLSourceAdapter):
    """Queryable local snapshot of an API source. Results carry ``provenance.mode = snapshot``."""

    def __init__(self, source_id: str, store: SnapshotStore, source_type: SourceType, name: str = "", description: str = "",
                 connector_name: str = ""):
        self.store = store
        self.connector_name = connector_name
        self.descriptor = SourceDescriptor(
            source_id=source_id, name=name or source_id, source_type=source_type, description=description,
            capabilities=SourceCapabilities(supports_schemas=False),
            connection_metadata={"mode": "synchronized_snapshot", "connector": connector_name})
        self._schema_cache: Dict[str, CollectionSchema] = {}

    @property
    def dialect(self) -> str:
        return "duckdb"

    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30):
        with self.store.lock:
            cur = self.store.conn.cursor()
            try:
                res = cur.execute(sql, params or [])
                cols = [d[0] for d in res.description] if res.description else []
                return cols, (res.fetchall() if res.description else [])
            finally:
                cur.close()

    def refresh(self) -> None:
        self._schema_cache.clear()

    def test_connection(self) -> bool:
        return True

    def discover_collections(self) -> List[str]:
        return self.store.tables()

    def provenance(self) -> Dict[str, Any]:
        states = self.store.all_states()
        last = max((s["last_sync"] for s in states if s.get("last_sync")), default=None)
        return {"mode": "snapshot", "connector": self.connector_name, "synced_at": last,
                "objects": {s["object"]: {"last_sync": s["last_sync"], "sync_mode": s["mode"], "status": s["status"]} for s in states}}

    def describe_collection(self, collection: str) -> CollectionSchema:
        if collection in self._schema_cache:
            return self._schema_cache[collection]
        if collection not in self.store.tables():
            raise KeyError(f"unknown collection {collection}")
        _c, rows = self._run(f'DESCRIBE "{collection}"')
        _c, cnt = self._run(f'SELECT COUNT(*) FROM "{collection}"')
        _c, meta = self._run("SELECT column_name, label, description, options, source_path FROM _backend_field_meta WHERE object = ?",
                             [collection])
        _c, rels = self._run("SELECT child_col, parent, parent_col, properties FROM _backend_relationships WHERE child = ?", [collection])
        _c, st = self._run("SELECT last_sync, mode FROM _backend_sync_state WHERE object = ?", [collection])
        m = {r[0]: r for r in meta}
        fks = {r[0]: (f"{r[1]}.{r[2]}", json.loads(r[3] or "{}")) for r in rels}
        cols = []
        for name, dtype, nullable, *_ in rows:
            label, desc, options, path = (m[name][1], m[name][2], m[name][3], m[name][4]) if name in m else (None, None, None, None)
            text = " ".join(x for x in [label or "", desc or ""] if x).strip()
            if options:
                text += f" Allowed values: {', '.join(str(o) for o in json.loads(options)[:20])}."
            if name.startswith("_backend_"):
                text = {"_backend_synced_at": "Time this row was last synchronized from the source",
                        "_backend_present": "False when a full refresh no longer returned this record (possible hard delete)",
                        "_backend_archived": "True when the record is archived/deleted in the source"}.get(name, text)
            cols.append(ColumnSchema(name=name, data_type=str(dtype), nullable=(nullable or "YES") == "YES",
                                     is_primary_key=name == "id", is_foreign_key=name in fks,
                                     foreign_key_target=fks[name][0] if name in fks else None,
                                     description=(text + (f" (source field {path})" if path and path != name else "")).strip()))
        synced = f" Snapshot synced {st[0][0]} ({st[0][1]})." if st else ""
        schema = CollectionSchema(source_id=self.source_id, collection_name=collection, full_name=collection,
                                  row_count=int(cnt[0][0]), columns=cols, primary_keys=["id"] if any(c.name == "id" for c in cols) else [],
                                  foreign_keys=[{"column": c, "references": t, "properties": p} for c, (t, p) in fks.items()],
                                  description=f"{self.connector_name} object '{collection}'.{synced}")
        self._schema_cache[collection] = schema
        return schema
