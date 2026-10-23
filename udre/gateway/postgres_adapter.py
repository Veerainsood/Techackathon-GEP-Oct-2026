"""PostgreSQL adapter: catalog discovery via information_schema / pg_catalog, read-only execution."""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from udre.contracts.models import CollectionSchema, ColumnSchema, SourceCapabilities, SourceDescriptor, SourceType
from udre.gateway.base import SQLSourceAdapter

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast", "udre_kg", "udre_memory")


class PostgresSourceAdapter(SQLSourceAdapter):
    def __init__(self, source_id: str, uri: str, name: str = "", description: str = "",
                 include_schemas: Optional[List[str]] = None, exclude_schemas: Optional[List[str]] = None,
                 capabilities: Optional[SourceCapabilities] = None):
        self.uri = uri
        self.include_schemas = include_schemas
        self.exclude_schemas = set(exclude_schemas or []) | set(SYSTEM_SCHEMAS)
        self.descriptor = SourceDescriptor(
            source_id=source_id, name=name or source_id, source_type=SourceType.POSTGRESQL, description=description,
            capabilities=capabilities or SourceCapabilities(supports_vector_search=True),
            connection_metadata={"uri": uri.split("@")[-1]})
        self._schema_cache: Dict[str, CollectionSchema] = {}
        self._collections: Optional[List[str]] = None
        self._pool_obj: Optional[ThreadedConnectionPool] = None
        self._pool_lock = threading.Lock()

    @property
    def dialect(self) -> str:
        return "postgres"

    def _pool(self) -> ThreadedConnectionPool:
        if self._pool_obj is None:
            with self._pool_lock:
                if self._pool_obj is None:
                    self._pool_obj = ThreadedConnectionPool(1, 16, self.uri, connect_timeout=5)
        return self._pool_obj

    placeholder = "%s"

    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30,
             snapshot: Optional[str] = None) -> Tuple[List[str], List[tuple]]:
        pool = self._pool()
        conn = pool.getconn()
        broken = False
        try:
            if snapshot:  # read inside an exported snapshot shared by a whole analysis (consistent view)
                conn.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
            elif not conn.readonly or conn.isolation_level != psycopg2.extensions.ISOLATION_LEVEL_DEFAULT:
                conn.set_session(isolation_level="DEFAULT", readonly=True, autocommit=False)
            with conn.cursor() as cur:
                if snapshot:
                    cur.execute("SET TRANSACTION SNAPSHOT %s", (snapshot,))
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall() if cur.description else []
            return cols, rows
        except psycopg2.InterfaceError:
            broken = True
            raise
        finally:
            if not broken:
                try:
                    conn.rollback()
                except psycopg2.Error:
                    broken = True
            pool.putconn(conn, close=broken)

    def export_snapshot(self):
        """Open a REPEATABLE READ transaction and export its snapshot. Returns (snapshot_id, holder connection).

        The holder must stay open while other connections import the snapshot; close it to release.
        """
        holder = psycopg2.connect(self.uri, connect_timeout=5)
        holder.set_session(isolation_level="REPEATABLE READ", readonly=True, autocommit=False)
        with holder.cursor() as cur:
            cur.execute("SELECT pg_export_snapshot()")
            snap = cur.fetchone()[0]
        return snap, holder

    def test_connection(self) -> bool:
        try:
            self._run("SELECT 1")
            self.descriptor.is_connected = True
        except Exception:
            self.descriptor.is_connected = False
        return self.descriptor.is_connected

    def discover_collections(self) -> List[str]:
        if self._collections is None:
            _c, rows = self._run(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type IN ('BASE TABLE', 'VIEW') ORDER BY 1, 2")
            out = []
            for schema, table in rows:
                if schema in self.exclude_schemas or schema.startswith("pg_"):
                    continue
                if self.include_schemas and schema not in self.include_schemas:
                    continue
                out.append(f"{schema}.{table}")
            self._collections = out
        return list(self._collections)

    def _load_catalog(self) -> None:
        """Load columns, comments, PKs and FKs for every collection in a few catalog queries."""
        names = set(self.discover_collections())
        _c, cols = self._run("""
            SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.is_nullable, c.column_default,
                   col_description(format('%I.%I', c.table_schema, c.table_name)::regclass, c.ordinal_position)
            FROM information_schema.columns c
            WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY c.table_schema, c.table_name, c.ordinal_position""")
        _c, tdesc = self._run("""
            SELECT n.nspname, c.relname, obj_description(c.oid, 'pg_class'), c.reltuples::bigint
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'v')""")
        _c, cons = self._run("""
            SELECT n.nspname, t.relname, con.contype, a.attname, fn.nspname, ft.relname, fa.attname
            FROM pg_constraint con
            JOIN pg_class t ON t.oid = con.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY k(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
            LEFT JOIN pg_class ft ON ft.oid = con.confrelid LEFT JOIN pg_namespace fn ON fn.oid = ft.relnamespace
            LEFT JOIN pg_attribute fa ON fa.attrelid = con.confrelid AND fa.attnum = con.confkey[k.ord]
            WHERE con.contype IN ('p', 'f')""")
        _c, idx = self._run("SELECT schemaname, tablename, indexdef FROM pg_indexes "
                            "WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")
        indexes: Dict[str, List[str]] = {}
        for s, t, d in idx:
            indexes.setdefault(f"{s}.{t}", []).append(d)
        desc = {f"{s}.{t}": (d or "", int(n or 0)) for s, t, d, n in tdesc}
        pks: Dict[str, set] = {}
        fks: Dict[str, Dict[str, str]] = {}
        for s, t, ctype, col, fs, ft, fc in cons:
            key = f"{s}.{t}"
            if ctype == "p":
                pks.setdefault(key, set()).add(col)
            else:
                fks.setdefault(key, {})[col] = f"{fs}.{ft}.{fc}"
        grouped: Dict[str, List[ColumnSchema]] = {}
        for s, t, col, dtype, nullable, default, cdesc in cols:
            key = f"{s}.{t}"
            if key not in names:
                continue
            fk = fks.get(key, {}).get(col)
            grouped.setdefault(key, []).append(ColumnSchema(
                name=col, data_type=dtype, nullable=nullable == "YES", is_primary_key=col in pks.get(key, set()),
                is_foreign_key=fk is not None, foreign_key_target=fk, default_value=default, description=cdesc or ""))
        for key, columns in grouped.items():
            schema, table = key.split(".", 1)
            self._schema_cache[key] = CollectionSchema(
                source_id=self.source_id, collection_name=table, schema_name=schema, full_name=key,
                row_count=max(desc.get(key, ("", 0))[1], 0), columns=columns, description=desc.get(key, ("", 0))[0],
                primary_keys=sorted(pks.get(key, set())),
                foreign_keys=[{"column": c, "references": r} for c, r in sorted(fks.get(key, {}).items())],
                indexes=sorted(indexes.get(key, [])))

    def describe_collection(self, collection: str) -> CollectionSchema:
        if not self._schema_cache:
            self._load_catalog()
        if collection not in self._schema_cache:
            raise KeyError(f"unknown collection {collection}")
        schema = self._schema_cache[collection]
        if schema.row_count <= 0:
            _c, rows = self._run(f"SELECT COUNT(*) FROM {self.quote(collection)}")
            schema.row_count = int(rows[0][0])
        return schema

    def refresh(self) -> None:
        self._schema_cache.clear()
        self._collections = None
