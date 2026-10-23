"""PostgreSQL adapter: catalog discovery via information_schema / pg_catalog, read-only execution."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2

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

    @property
    def dialect(self) -> str:
        return "postgres"

    def _connect(self):
        return psycopg2.connect(self.uri, connect_timeout=5)

    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30) -> Tuple[List[str], List[tuple]]:
        conn = self._connect()
        try:
            conn.set_session(readonly=True, autocommit=False)
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
                cur.execute(sql, params)
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall() if cur.description else []
            conn.rollback()
            return cols, rows
        finally:
            conn.close()

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
                foreign_keys=[{"column": c, "references": r} for c, r in sorted(fks.get(key, {}).items())])

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
