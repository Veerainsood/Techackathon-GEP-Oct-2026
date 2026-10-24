"""Persistent learning store (PostgreSQL + pgvector).

Stores only outcomes backed by execution evidence: adjudicated query templates, failed query
patterns (with their errors), value mappings resolved against real column values, and metric
usages.  Each item carries the schema version it was learned on; items from an older version
are excluded from retrieval and marked stale on refresh.  Reads are restricted to the caller's
tenant and to tenant-shared items or the caller's own items.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional


from backend.gateway.registry import AccessContext
from backend.ontology.embedding import DIM, embed, to_pgvector
from backend.pg_pool import shared_pool

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS backend_memory;
CREATE TABLE IF NOT EXISTS backend_memory.learned_items (
    item_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT,                 -- NULL = shared with the whole tenant
    kind TEXT NOT NULL,           -- query_template | failed_query | value_mapping | metric_usage | schema_snapshot
    source_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    question TEXT,
    item_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'verified',
    uses INTEGER NOT NULL DEFAULT 0,
    embedding vector({DIM}),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS learned_items_scope_idx ON backend_memory.learned_items (tenant_id, kind, source_id);
"""

KINDS = {"query_template", "failed_query", "value_mapping", "metric_usage", "schema_snapshot"}


class LearningStore:
    def __init__(self, uri: str):
        self._pool = shared_pool(uri)
        self._exec(DDL)

    def _exec(self, sql: str, params: Any = None, fetch: bool = False):
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall() if fetch else None
            conn.commit()
            return rows
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    # ------------------------------------------------------------------ writes
    def remember(self, ctx: AccessContext, kind: str, source_id: str, schema_version: str, key: str,
                 payload: Dict[str, Any], question: Optional[str] = None, evidence_refs: Optional[List[str]] = None,
                 shared: bool = True, status: str = "verified") -> str:
        if kind not in KINDS:
            raise ValueError(kind)
        user = None if shared else ctx.user_id
        item_id = hashlib.sha1(f"{ctx.tenant_id}|{user}|{kind}|{source_id}|{key}".encode()).hexdigest()[:24]
        self._exec("""
            INSERT INTO backend_memory.learned_items (item_id, tenant_id, user_id, kind, source_id, schema_version, question,
                item_key, payload, evidence_refs, status, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
            ON CONFLICT (item_id) DO UPDATE SET payload = EXCLUDED.payload, schema_version = EXCLUDED.schema_version,
                status = EXCLUDED.status, evidence_refs = EXCLUDED.evidence_refs, question = EXCLUDED.question,
                embedding = EXCLUDED.embedding, uses = backend_memory.learned_items.uses + 1, updated_at = now()""",
                   (item_id, ctx.tenant_id, user, kind, source_id, schema_version, question, key,
                    json.dumps(payload, default=str), json.dumps(evidence_refs or []), status,
                    to_pgvector(embed(question or key))))
        return item_id

    # ------------------------------------------------------------------ reads
    def _scope(self) -> str:
        return "tenant_id = %s AND (user_id IS NULL OR user_id = %s) AND status <> 'stale'"

    def similar(self, ctx: AccessContext, text: str, kind: str, versions: Dict[str, str], k: int = 5,
                min_score: float = 0.5) -> List[Dict[str, Any]]:
        """Similar items of ``kind`` learned on the *current* schema version of each source."""
        if not versions:
            return []
        vec = to_pgvector(embed(text))
        rows = self._exec(f"""
            SELECT item_id, source_id, schema_version, question, item_key, payload, status, 1 - (embedding <=> %s::vector)
            FROM backend_memory.learned_items WHERE {self._scope()} AND kind = %s AND source_id = ANY(%s)
            ORDER BY embedding <=> %s::vector LIMIT %s""",
                          (vec, ctx.tenant_id, ctx.user_id, kind, list(versions), vec, k * 3), fetch=True)
        out = []
        for item_id, sid, ver, q, key, payload, status, score in rows:
            if versions.get(sid) != ver or score < min_score:
                continue
            out.append(dict(item_id=item_id, source_id=sid, question=q, key=key, payload=payload, status=status,
                            score=round(float(score), 4)))
        return out[:k]

    def by_kind(self, ctx: AccessContext, kind: str, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = (f"SELECT item_key, source_id, schema_version, payload, uses, question FROM backend_memory.learned_items "
               f"WHERE {self._scope()} AND kind = %s")
        params: List[Any] = [ctx.tenant_id, ctx.user_id, kind]
        if source_id:
            sql += " AND source_id = %s"
            params.append(source_id)
        rows = self._exec(sql + " ORDER BY uses DESC, updated_at DESC", params, fetch=True)
        return [dict(key=k, source_id=s, schema_version=v, payload=p, uses=u, question=q) for k, s, v, p, u, q in rows]

    def value_mappings(self, ctx: AccessContext, versions: Dict[str, str]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for item in self.by_kind(ctx, "value_mapping"):
            if versions.get(item["source_id"]) == item["schema_version"]:
                out.setdefault(item["payload"]["term"], []).append(dict(item["payload"], source_id=item["source_id"]))
        return out

    # ------------------------------------------------------------------ lifecycle
    def invalidate_source(self, source_id: str, current_version: str) -> int:
        rows = self._exec("UPDATE backend_memory.learned_items SET status = 'stale', updated_at = now() "
                          "WHERE source_id = %s AND schema_version <> %s AND status <> 'stale' RETURNING item_id",
                          (source_id, current_version), fetch=True)
        return len(rows)

    def clear(self, ctx: AccessContext) -> None:
        self._exec("DELETE FROM backend_memory.learned_items WHERE tenant_id = %s", (ctx.tenant_id,))

    def stats(self, ctx: AccessContext) -> Dict[str, int]:
        rows = self._exec("SELECT kind || ':' || status, COUNT(*) FROM backend_memory.learned_items WHERE tenant_id = %s "
                          "GROUP BY 1", (ctx.tenant_id,), fetch=True)
        return {k: n for k, n in rows}
