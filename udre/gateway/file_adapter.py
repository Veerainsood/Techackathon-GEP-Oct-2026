"""DuckDB-backed adapter for structured files (CSV, Excel, JSON, Parquet).

Each file (or Excel sheet) becomes one collection exposed as a DuckDB view.  Files are never
modified; the in-memory DuckDB database only holds views over them.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import duckdb
import pandas as pd

from udre.contracts.models import CollectionSchema, ColumnSchema, SourceCapabilities, SourceDescriptor, SourceType
from udre.gateway.base import SQLSourceAdapter

EXT_TYPES = {".csv": SourceType.CSV, ".tsv": SourceType.CSV, ".parquet": SourceType.PARQUET,
             ".json": SourceType.JSON, ".jsonl": SourceType.JSON, ".ndjson": SourceType.JSON,
             ".xlsx": SourceType.EXCEL, ".xls": SourceType.EXCEL}


def _safe_name(stem: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", stem).strip("_").lower()
    return name if name and not name[0].isdigit() else f"t_{name}"


class FileSourceAdapter(SQLSourceAdapter):
    def __init__(self, source_id: str, paths: Sequence[str], name: str = "", description: str = "",
                 capabilities: Optional[SourceCapabilities] = None):
        files = [Path(p) for p in paths]
        expanded: List[Path] = []
        for p in files:
            expanded.extend(sorted(q for q in p.iterdir() if q.suffix.lower() in EXT_TYPES) if p.is_dir() else [p])
        if not expanded:
            raise ValueError("no supported files supplied")
        kinds = {EXT_TYPES[p.suffix.lower()] for p in expanded}
        self.descriptor = SourceDescriptor(
            source_id=source_id, name=name or source_id,
            source_type=kinds.pop() if len(kinds) == 1 else SourceType.CSV, description=description,
            capabilities=capabilities or SourceCapabilities(supports_schemas=False),
            connection_metadata={"files": [str(p) for p in expanded]})
        self._conn = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self._files: Dict[str, str] = {}
        self._schema_cache: Dict[str, CollectionSchema] = {}
        for p in expanded:
            self._register(p)

    @property
    def dialect(self) -> str:
        return "duckdb"

    def _register(self, path: Path) -> None:
        ext = path.suffix.lower()
        p = str(path).replace("'", "''")
        if ext in (".xlsx", ".xls"):
            sheets = pd.read_excel(path, sheet_name=None)
            for sheet, df in sheets.items():
                name = _safe_name(path.stem if len(sheets) == 1 else f"{path.stem}_{sheet}")
                self._conn.register("__udre_df", df)
                self._conn.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "__udre_df"')
                self._conn.unregister("__udre_df")
                self._files[name] = f"{path}#{sheet}"
            return
        reader = {".csv": f"read_csv_auto('{p}', header=true)", ".tsv": f"read_csv_auto('{p}', delim='\t', header=true)",
                  ".parquet": f"read_parquet('{p}')", ".json": f"read_json_auto('{p}')",
                  ".jsonl": f"read_json_auto('{p}', format='newline_delimited')",
                  ".ndjson": f"read_json_auto('{p}', format='newline_delimited')"}[ext]
        name = _safe_name(path.stem)
        self._conn.execute(f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM {reader}')
        self._files[name] = str(path)

    def add_file(self, path: str) -> List[str]:
        before = set(self._files)
        self._register(Path(path))
        self._schema_cache.clear()
        return sorted(set(self._files) - before)

    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30) -> Tuple[List[str], List[tuple]]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                res = cur.execute(sql, params or [])
                cols = [d[0] for d in res.description] if res.description else []
                rows = res.fetchall() if res.description else []
                return cols, rows
            finally:
                cur.close()

    def test_connection(self) -> bool:
        try:
            self._run("SELECT 1")
            self.descriptor.is_connected = True
        except Exception:
            self.descriptor.is_connected = False
        return self.descriptor.is_connected

    def discover_collections(self) -> List[str]:
        return sorted(self._files)

    def describe_collection(self, collection: str) -> CollectionSchema:
        if collection in self._schema_cache:
            return self._schema_cache[collection]
        if collection not in self._files:
            raise KeyError(f"unknown collection {collection}")
        _c, rows = self._run(f'DESCRIBE "{collection}"')
        _c, cnt = self._run(f'SELECT COUNT(*) FROM "{collection}"')
        cols = [ColumnSchema(name=r[0], data_type=str(r[1]), nullable=(r[2] or "YES") == "YES") for r in rows]
        schema = CollectionSchema(source_id=self.source_id, collection_name=collection, full_name=collection,
                                  row_count=int(cnt[0][0]), columns=cols,
                                  description=f"Uploaded file {Path(self._files[collection].split('#')[0]).name}")
        self._schema_cache[collection] = schema
        return schema
