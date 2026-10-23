"""Persistent knowledge graph on PostgreSQL relational tables + pgvector.

Two connected layers live in the same tables, distinguished by node type:
  * physical: Source, Collection, Column, Relationship
  * semantic: Concept, Metric, Dimension, ValueMapping, QueryPattern
EvidenceRecord rows back every verification.  A node/edge can only become ``verified_fact``
through ``promote`` with deterministic evidence (query results, schema constraints,
reconciliation checks) -- never from model-generated text alone.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2.extras import execute_values
from psycopg2.pool import ThreadedConnectionPool

from backend.contracts.models import (
    EdgeType,
    EvidenceReference,
    GraphEdge,
    GraphNode,
    NodeType,
    VerificationStatus,
)
from backend.ontology.embedding import DIM, embed, to_pgvector

VERIFIABLE_EVIDENCE = {"query_result", "schema_constraint", "reconciliation_check", "column_profile", "row_sample"}

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS backend_kg;
CREATE TABLE IF NOT EXISTS backend_kg.kg_evidence (
    evidence_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    source_id TEXT,
    collection_name TEXT,
    column_name TEXT,
    evidence_type TEXT NOT NULL,
    query_or_action TEXT NOT NULL,
    observed_summary TEXT NOT NULL,
    sample_json JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS backend_kg.kg_nodes (
    tenant_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL,
    source_id TEXT,
    properties JSONB NOT NULL DEFAULT '{{}}',
    provenance TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]',
    last_validated_at TIMESTAMPTZ NOT NULL,
    embedding vector({DIM}),
    PRIMARY KEY (tenant_id, node_id)
);
CREATE TABLE IF NOT EXISTS backend_kg.kg_edges (
    tenant_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    source_id TEXT,
    properties JSONB NOT NULL DEFAULT '{{}}',
    provenance TEXT NOT NULL,
    evidence_refs JSONB NOT NULL DEFAULT '[]',
    verification_status TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    last_validated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, edge_id)
);
CREATE INDEX IF NOT EXISTS kg_nodes_type_idx ON backend_kg.kg_nodes (tenant_id, node_type);
CREATE INDEX IF NOT EXISTS kg_nodes_source_idx ON backend_kg.kg_nodes (tenant_id, source_id);
CREATE INDEX IF NOT EXISTS kg_edges_src_idx ON backend_kg.kg_edges (tenant_id, source_node_id);
CREATE INDEX IF NOT EXISTS kg_edges_dst_idx ON backend_kg.kg_edges (tenant_id, target_node_id);
CREATE INDEX IF NOT EXISTS kg_edges_type_idx ON backend_kg.kg_edges (tenant_id, edge_type);
"""


class PromotionRefused(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeGraph:
    def __init__(self, uri: str):
        self.uri = uri
        self._pool = ThreadedConnectionPool(1, 12, uri, connect_timeout=5)
        self._lock = threading.Lock()
        with self._cursor() as cur:
            cur.execute(DDL)

    # ------------------------------------------------------------------ plumbing
    class _Cur:
        def __init__(self, kg: "KnowledgeGraph"):
            self.kg = kg

        def __enter__(self):
            self.conn = self.kg._pool.getconn()
            self.cur = self.conn.cursor()
            return self.cur

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
            finally:
                self.cur.close()
                self.kg._pool.putconn(self.conn)

    def _cursor(self) -> "KnowledgeGraph._Cur":
        return KnowledgeGraph._Cur(self)

    def close(self) -> None:
        self._pool.closeall()

    # ------------------------------------------------------------------ evidence
    def add_evidence(self, evidence: Sequence[EvidenceReference], tenant: str = "default") -> None:
        if not evidence:
            return
        rows = [(e.evidence_id, tenant, e.source_id, e.collection_name, e.column_name, e.evidence_type,
                 e.query_or_action[:4000], e.observed_summary[:2000], json.dumps(e.sample_data[:10], default=str))
                for e in evidence]
        with self._cursor() as cur:
            execute_values(cur, """
                INSERT INTO backend_kg.kg_evidence (evidence_id, tenant_id, source_id, collection_name, column_name,
                    evidence_type, query_or_action, observed_summary, sample_json) VALUES %s
                ON CONFLICT (evidence_id) DO UPDATE SET observed_summary = EXCLUDED.observed_summary,
                    sample_json = EXCLUDED.sample_json, created_at = now()""", rows)

    def get_evidence(self, evidence_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = list(evidence_ids)
        if not ids:
            return []
        with self._cursor() as cur:
            cur.execute("SELECT evidence_id, evidence_type, source_id, query_or_action, observed_summary "
                        "FROM backend_kg.kg_evidence WHERE evidence_id = ANY(%s)", (ids,))
            return [dict(zip(["evidence_id", "evidence_type", "source_id", "query_or_action", "observed_summary"], r))
                    for r in cur.fetchall()]

    # ------------------------------------------------------------------ nodes / edges
    def upsert_nodes(self, nodes: Sequence[GraphNode], tenant: str = "default",
                     embed_texts: Optional[Dict[str, str]] = None) -> None:
        if not nodes:
            return
        embed_texts = embed_texts or {}
        rows = []
        for n in nodes:
            text = embed_texts.get(n.node_id) or f"{n.label} {n.properties.get('description', '')}"
            rows.append((tenant, n.node_id, n.node_type.value, n.label, n.source_id,
                         json.dumps(n.properties, default=str), n.provenance, n.verification_status.value,
                         n.schema_version, n.confidence, json.dumps(n.evidence_refs), n.last_validated_at,
                         to_pgvector(embed(text))))
        with self._cursor() as cur:
            execute_values(cur, """
                INSERT INTO backend_kg.kg_nodes (tenant_id, node_id, node_type, label, source_id, properties, provenance,
                    verification_status, schema_version, confidence, evidence_refs, last_validated_at, embedding)
                VALUES %s
                ON CONFLICT (tenant_id, node_id) DO UPDATE SET
                    label = EXCLUDED.label, properties = EXCLUDED.properties, provenance = EXCLUDED.provenance,
                    schema_version = EXCLUDED.schema_version, embedding = EXCLUDED.embedding,
                    evidence_refs = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM jsonb_array_elements(
                        backend_kg.kg_nodes.evidence_refs || EXCLUDED.evidence_refs) e), '[]'::jsonb),
                    -- never silently downgrade a verified fact; never auto-upgrade without promote()
                    verification_status = CASE
                        WHEN backend_kg.kg_nodes.verification_status = 'verified_fact'
                             AND backend_kg.kg_nodes.schema_version = EXCLUDED.schema_version THEN 'verified_fact'
                        ELSE EXCLUDED.verification_status END,
                    confidence = GREATEST(EXCLUDED.confidence,
                        CASE WHEN backend_kg.kg_nodes.schema_version = EXCLUDED.schema_version
                             THEN backend_kg.kg_nodes.confidence ELSE 0 END),
                    last_validated_at = EXCLUDED.last_validated_at""",
                rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)")

    def upsert_edges(self, edges: Sequence[GraphEdge], tenant: str = "default", source_id: Optional[str] = None) -> None:
        if not edges:
            return
        rows = [(tenant, e.edge_id, e.source_node_id, e.target_node_id, e.edge_type.value,
                 e.properties.get("source_id", source_id), json.dumps(e.properties, default=str), e.provenance,
                 json.dumps(e.evidence_refs), e.verification_status.value, e.schema_version, e.confidence,
                 e.last_validated_at) for e in edges]
        with self._cursor() as cur:
            execute_values(cur, """
                INSERT INTO backend_kg.kg_edges (tenant_id, edge_id, source_node_id, target_node_id, edge_type, source_id,
                    properties, provenance, evidence_refs, verification_status, schema_version, confidence,
                    last_validated_at) VALUES %s
                ON CONFLICT (tenant_id, edge_id) DO UPDATE SET
                    properties = EXCLUDED.properties, provenance = EXCLUDED.provenance,
                    evidence_refs = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM jsonb_array_elements(
                        backend_kg.kg_edges.evidence_refs || EXCLUDED.evidence_refs) e), '[]'::jsonb),
                    verification_status = CASE
                        WHEN backend_kg.kg_edges.verification_status = 'verified_fact'
                             AND backend_kg.kg_edges.schema_version = EXCLUDED.schema_version THEN 'verified_fact'
                        ELSE EXCLUDED.verification_status END,
                    schema_version = EXCLUDED.schema_version, confidence = EXCLUDED.confidence,
                    last_validated_at = EXCLUDED.last_validated_at""", rows)

    def promote(self, item_id: str, evidence_ids: Sequence[str], is_edge: bool = False, tenant: str = "default") -> None:
        """Mark a node/edge as verified. Requires at least one stored deterministic evidence record."""
        evidence = self.get_evidence(evidence_ids)
        usable = [e for e in evidence if e["evidence_type"] in VERIFIABLE_EVIDENCE]
        if not usable:
            raise PromotionRefused(f"{item_id}: no deterministic evidence among {list(evidence_ids)}")
        table, key = ("kg_edges", "edge_id") if is_edge else ("kg_nodes", "node_id")
        with self._cursor() as cur:
            cur.execute(f"""
                UPDATE backend_kg.{table} SET verification_status = 'verified_fact', last_validated_at = now(),
                    confidence = GREATEST(confidence, 0.95),
                    evidence_refs = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM jsonb_array_elements(evidence_refs || %s::jsonb) e), '[]'::jsonb)
                WHERE tenant_id = %s AND {key} = %s""",
                        (json.dumps([e["evidence_id"] for e in usable]), tenant, item_id))
            if cur.rowcount == 0:
                raise KeyError(item_id)

    def set_status(self, item_id: str, status: VerificationStatus, is_edge: bool = False, tenant: str = "default") -> None:
        if status == VerificationStatus.VERIFIED_FACT:
            raise PromotionRefused("use promote() with evidence to verify")
        table, key = ("kg_edges", "edge_id") if is_edge else ("kg_nodes", "node_id")
        with self._cursor() as cur:
            cur.execute(f"UPDATE backend_kg.{table} SET verification_status = %s, last_validated_at = now() "
                        f"WHERE tenant_id = %s AND {key} = %s", (status.value, tenant, item_id))

    # ------------------------------------------------------------------ reads
    _NODE_COLS = ("node_id", "node_type", "label", "source_id", "properties", "provenance", "verification_status",
                  "schema_version", "confidence", "evidence_refs", "last_validated_at")
    _EDGE_COLS = ("edge_id", "source_node_id", "target_node_id", "edge_type", "properties", "provenance",
                  "evidence_refs", "verification_status", "schema_version", "confidence", "last_validated_at")

    def _node(self, row: tuple) -> Dict[str, Any]:
        return dict(zip(self._NODE_COLS, row))

    def get_node(self, node_id: str, tenant: str = "default") -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(f"SELECT {', '.join(self._NODE_COLS)} FROM backend_kg.kg_nodes WHERE tenant_id = %s AND node_id = %s",
                        (tenant, node_id))
            row = cur.fetchone()
        return self._node(row) if row else None

    def nodes(self, node_type: Optional[NodeType] = None, source_id: Optional[str] = None, tenant: str = "default",
              status: Optional[VerificationStatus] = None) -> List[Dict[str, Any]]:
        sql = f"SELECT {', '.join(self._NODE_COLS)} FROM backend_kg.kg_nodes WHERE tenant_id = %s"
        params: List[Any] = [tenant]
        if node_type:
            sql += " AND node_type = %s"
            params.append(node_type.value)
        if source_id:
            sql += " AND source_id = %s"
            params.append(source_id)
        if status:
            sql += " AND verification_status = %s"
            params.append(status.value)
        with self._cursor() as cur:
            cur.execute(sql + " ORDER BY node_id", params)
            return [self._node(r) for r in cur.fetchall()]

    def edges(self, edge_type: Optional[EdgeType] = None, source_node_id: Optional[str] = None,
              target_node_id: Optional[str] = None, source_id: Optional[str] = None,
              tenant: str = "default") -> List[Dict[str, Any]]:
        sql = f"SELECT {', '.join(self._EDGE_COLS)} FROM backend_kg.kg_edges WHERE tenant_id = %s"
        params: List[Any] = [tenant]
        for col, val in (("edge_type", edge_type.value if edge_type else None), ("source_node_id", source_node_id),
                         ("target_node_id", target_node_id), ("source_id", source_id)):
            if val:
                sql += f" AND {col} = %s"
                params.append(val)
        with self._cursor() as cur:
            cur.execute(sql + " ORDER BY edge_id", params)
            return [dict(zip(self._EDGE_COLS, r)) for r in cur.fetchall()]

    def search(self, text: str, node_types: Optional[Sequence[NodeType]] = None, k: int = 10,
               source_id: Optional[str] = None, tenant: str = "default",
               exclude_stale: bool = True) -> List[Tuple[Dict[str, Any], float]]:
        """Vector similarity search (pgvector cosine distance)."""
        sql = (f"SELECT {', '.join(self._NODE_COLS)}, 1 - (embedding <=> %s::vector) AS score "
               "FROM backend_kg.kg_nodes WHERE tenant_id = %s")
        params: List[Any] = [to_pgvector(embed(text)), tenant]
        if node_types:
            sql += " AND node_type = ANY(%s)"
            params.append([t.value for t in node_types])
        if source_id:
            sql += " AND source_id = %s"
            params.append(source_id)
        if exclude_stale:
            sql += " AND verification_status <> 'stale_schema'"
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        params += [params[0], k]
        with self._cursor() as cur:
            cur.execute(sql, params)
            return [(self._node(r[:-1]), float(r[-1])) for r in cur.fetchall()]

    def neighbors(self, node_id: str, edge_types: Optional[Sequence[EdgeType]] = None, depth: int = 2,
                  tenant: str = "default") -> List[Dict[str, Any]]:
        """Undirected traversal up to ``depth`` hops using a recursive CTE."""
        types = [t.value for t in edge_types] if edge_types else None
        with self._cursor() as cur:
            cur.execute("""
                WITH RECURSIVE walk(node_id, depth, via_edge, path) AS (
                    SELECT %s::text, 0, NULL::text, ARRAY[%s::text]
                    UNION ALL
                    SELECT CASE WHEN e.source_node_id = w.node_id THEN e.target_node_id ELSE e.source_node_id END,
                           w.depth + 1, e.edge_type,
                           w.path || CASE WHEN e.source_node_id = w.node_id THEN e.target_node_id ELSE e.source_node_id END
                    FROM walk w JOIN backend_kg.kg_edges e
                      ON e.tenant_id = %s AND (e.source_node_id = w.node_id OR e.target_node_id = w.node_id)
                    WHERE w.depth < %s AND (%s::text[] IS NULL OR e.edge_type = ANY(%s::text[]))
                      AND NOT (CASE WHEN e.source_node_id = w.node_id THEN e.target_node_id ELSE e.source_node_id END = ANY(w.path))
                )
                SELECT DISTINCT ON (w.node_id) w.node_id, w.depth, w.via_edge, n.node_type, n.label, n.verification_status
                FROM walk w JOIN backend_kg.kg_nodes n ON n.tenant_id = %s AND n.node_id = w.node_id
                WHERE w.depth > 0 ORDER BY w.node_id, w.depth""",
                        (node_id, node_id, tenant, depth, types, types, tenant))
            return [dict(zip(["node_id", "depth", "via_edge", "node_type", "label", "verification_status"], r))
                    for r in cur.fetchall()]

    # ------------------------------------------------------------------ lifecycle
    def invalidate_source(self, source_id: str, current_version: str, tenant: str = "default") -> Dict[str, int]:
        """Mark knowledge derived from an older schema version of ``source_id`` as stale."""
        with self._cursor() as cur:
            cur.execute("UPDATE backend_kg.kg_nodes SET verification_status = 'stale_schema' WHERE tenant_id = %s "
                        "AND source_id = %s AND schema_version <> %s AND verification_status <> 'stale_schema'",
                        (tenant, source_id, current_version))
            n = cur.rowcount
            cur.execute("UPDATE backend_kg.kg_edges SET verification_status = 'stale_schema' WHERE tenant_id = %s "
                        "AND source_id = %s AND schema_version <> %s AND verification_status <> 'stale_schema'",
                        (tenant, source_id, current_version))
            return {"nodes": n, "edges": cur.rowcount}

    def source_version(self, source_id: str, tenant: str = "default") -> Optional[str]:
        node = self.get_node(f"src:{source_id}", tenant)
        return node["schema_version"] if node else None

    def delete_tenant(self, tenant: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM backend_kg.kg_edges WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM backend_kg.kg_nodes WHERE tenant_id = %s", (tenant,))
            cur.execute("DELETE FROM backend_kg.kg_evidence WHERE tenant_id = %s", (tenant,))

    def stats(self, tenant: str = "default") -> Dict[str, Any]:
        with self._cursor() as cur:
            cur.execute("SELECT node_type, verification_status, COUNT(*) FROM backend_kg.kg_nodes WHERE tenant_id = %s "
                        "GROUP BY 1, 2 ORDER BY 1, 2", (tenant,))
            nodes = [{"type": t, "status": s, "count": c} for t, s, c in cur.fetchall()]
            cur.execute("SELECT edge_type, verification_status, COUNT(*) FROM backend_kg.kg_edges WHERE tenant_id = %s "
                        "GROUP BY 1, 2 ORDER BY 1, 2", (tenant,))
            edges = [{"type": t, "status": s, "count": c} for t, s, c in cur.fetchall()]
            cur.execute("SELECT COUNT(*) FROM backend_kg.kg_evidence WHERE tenant_id = %s", (tenant,))
            ev = cur.fetchone()[0]
        return {"nodes": nodes, "edges": edges, "evidence_records": ev}
