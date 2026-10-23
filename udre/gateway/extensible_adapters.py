"""Extension adapters: MongoDB (native aggregation pipelines) and scoped REST APIs.

Neither is forced into SQL: MongoDB executes JSON aggregation pipelines natively, while the REST
connector materializes explicitly allow-listed GET endpoints into DuckDB for analysis.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import duckdb
import pandas as pd

from udre.contracts.models import (
    CollectionSchema,
    ColumnProfile,
    ColumnSchema,
    QueryExecutionResult,
    SourceCapabilities,
    SourceDescriptor,
    SourceType,
    ValueMatch,
)
from udre.gateway.base import DataSourceAdapter, SQLSourceAdapter, jsonable, normalize_text


class MongoSourceAdapter(DataSourceAdapter):
    """MongoDB adapter. ``execute`` accepts ``{"collection": ..., "pipeline": [...]}`` as JSON.

    Requires ``pymongo`` (optional dependency). Write stages ($out, $merge) are rejected.
    """

    FORBIDDEN_STAGES = {"$out", "$merge"}

    def __init__(self, source_id: str, uri: str, database: str, name: str = ""):
        try:
            import pymongo  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError("MongoSourceAdapter requires `pip install pymongo`") from exc
        import pymongo

        self._db = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000)[database]
        self.descriptor = SourceDescriptor(
            source_id=source_id, name=name or source_id, source_type=SourceType.MONGODB,
            capabilities=SourceCapabilities(supports_sql=False, supports_explain=False, supports_schemas=False,
                                            supports_document_pipeline=True),
            connection_metadata={"database": database})

    def test_connection(self) -> bool:
        try:
            self._db.command("ping")
            return True
        except Exception:
            return False

    def discover_collections(self) -> List[str]:
        return sorted(self._db.list_collection_names())

    def describe_collection(self, collection: str) -> CollectionSchema:
        docs = list(self._db[collection].find({}, limit=200))
        fields: Dict[str, str] = {}
        for d in docs:
            for k, v in d.items():
                fields.setdefault(k, type(v).__name__)
        return CollectionSchema(source_id=self.source_id, collection_name=collection, full_name=collection,
                                row_count=self._db[collection].estimated_document_count(),
                                columns=[ColumnSchema(name=k, data_type=t) for k, t in fields.items()],
                                primary_keys=["_id"])

    def profile_column(self, collection: str, column: str, top_k: int = 15) -> ColumnProfile:
        coll = self._db[collection]
        total = coll.estimated_document_count()
        top = list(coll.aggregate([{"$group": {"_id": f"${column}", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": top_k}]))
        nulls = coll.count_documents({column: None})
        distinct = len(coll.distinct(column)) if total < 200_000 else -1
        return ColumnProfile(source_id=self.source_id, collection_name=collection, column_name=column, data_type="mixed",
                             total_count=total, null_count=nulls, null_ratio=nulls / total if total else 0,
                             distinct_count=distinct, is_categorical=0 < distinct <= 60,
                             top_values=[{"value": jsonable(t["_id"]), "count": t["n"]} for t in top])

    def sample_rows(self, collection: str, n: int = 5) -> QueryExecutionResult:
        return self.execute(json.dumps({"collection": collection, "pipeline": [{"$limit": n}]}), max_rows=n)

    def search_values(self, collection: str, column: str, term: str, limit: int = 10) -> List[ValueMatch]:
        values = self._db[collection].distinct(column)
        nterm = normalize_text(term)
        return [ValueMatch(source_id=self.source_id, collection_name=collection, column_name=column, matched_value=v,
                           similarity_score=1.0 if normalize_text(v) == nterm else 0.85, match_type="substring")
                for v in values if nterm in normalize_text(v)][:limit]

    def execute(self, query: str, max_rows: int = 1000, timeout_s: int = 30) -> QueryExecutionResult:
        t0 = time.perf_counter()
        try:
            spec = json.loads(query)
            pipeline = spec["pipeline"]
            if any(k in self.FORBIDDEN_STAGES for stage in pipeline for k in stage):
                raise PermissionError("write stages are not permitted")
            pipeline = pipeline + [{"$limit": max_rows + 1}]
            docs = list(self._db[spec["collection"]].aggregate(pipeline, maxTimeMS=timeout_s * 1000))
            rows = [{k: jsonable(v) for k, v in d.items()} for d in docs[:max_rows]]
            cols = sorted({k for r in rows for k in r})
            return QueryExecutionResult(source_id=self.source_id, query=query, succeeded=True, rows=rows, columns=cols,
                                        row_count=len(rows), truncated=len(docs) > max_rows,
                                        execution_time_ms=(time.perf_counter() - t0) * 1000)
        except Exception as exc:
            return QueryExecutionResult(source_id=self.source_id, query=query, succeeded=False,
                                        error_message=f"{type(exc).__name__}: {exc}",
                                        execution_time_ms=(time.perf_counter() - t0) * 1000)


class RestApiSourceAdapter(SQLSourceAdapter):
    """Scoped REST connector.

    Only explicitly configured GET endpoints on allow-listed hosts are reachable. Each endpoint
    (``{"name": ..., "url": ..., "records_path": "data.items"}``) is fetched once on first use,
    flattened, and exposed to SQL through DuckDB.
    """

    def __init__(self, source_id: str, endpoints: List[Dict[str, Any]], allowed_hosts: List[str],
                 headers: Optional[Dict[str, str]] = None, name: str = "", fetcher=None):
        for ep in endpoints:
            host = urlparse(ep["url"]).hostname
            if host not in allowed_hosts:
                raise PermissionError(f"endpoint host {host} is not in the allow-list")
        self.endpoints = {ep["name"]: ep for ep in endpoints}
        self.headers = headers or {}
        self._fetcher = fetcher
        self._conn = duckdb.connect(":memory:")
        self._lock = threading.Lock()
        self._loaded: Dict[str, bool] = {}
        self._schema_cache: Dict[str, CollectionSchema] = {}
        self.descriptor = SourceDescriptor(
            source_id=source_id, name=name or source_id, source_type=SourceType.REST_API,
            capabilities=SourceCapabilities(supports_schemas=False),
            connection_metadata={"endpoints": list(self.endpoints), "allowed_hosts": allowed_hosts})

    @property
    def dialect(self) -> str:
        return "duckdb"

    def _fetch(self, ep: Dict[str, Any]) -> Any:
        if self._fetcher:
            return self._fetcher(ep["url"])
        import httpx

        resp = httpx.get(ep["url"], headers=self.headers, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def _ensure(self, name: str) -> None:
        if self._loaded.get(name):
            return
        payload = self._fetch(self.endpoints[name])
        for key in filter(None, (self.endpoints[name].get("records_path") or "").split(".")):
            payload = payload[key]
        df = pd.json_normalize(payload if isinstance(payload, list) else [payload], sep="_")
        with self._lock:
            self._conn.register("__udre_api", df)
            self._conn.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "__udre_api"')
            self._conn.unregister("__udre_api")
        self._loaded[name] = True

    def _run(self, sql: str, params: Optional[Sequence[Any]] = None, timeout_s: int = 30) -> Tuple[List[str], List[tuple]]:
        for name in self.endpoints:
            if name in sql:
                self._ensure(name)
        with self._lock:
            cur = self._conn.cursor()
            try:
                res = cur.execute(sql, params or [])
                cols = [d[0] for d in res.description] if res.description else []
                return cols, (res.fetchall() if res.description else [])
            finally:
                cur.close()

    def test_connection(self) -> bool:
        return True

    def discover_collections(self) -> List[str]:
        return sorted(self.endpoints)

    def describe_collection(self, collection: str) -> CollectionSchema:
        if collection not in self._schema_cache:
            self._ensure(collection)
            _c, rows = self._run(f'DESCRIBE "{collection}"')
            _c, cnt = self._run(f'SELECT COUNT(*) FROM "{collection}"')
            self._schema_cache[collection] = CollectionSchema(
                source_id=self.source_id, collection_name=collection, full_name=collection, row_count=int(cnt[0][0]),
                columns=[ColumnSchema(name=r[0], data_type=str(r[1])) for r in rows],
                description=f"REST endpoint {self.endpoints[collection]['url']}")
        return self._schema_cache[collection]
