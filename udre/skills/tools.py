"""The fixed 12-tool API shared by every agent.

Collections are never wrapped in collection-specific tools; they are discovered as lightweight
skill descriptors (stored on Collection nodes in the knowledge graph) and addressed by name.
A ``ToolSession`` binds the toolbox to one agent run: every call must state the investigation
objective it serves, is authorized through the gateway, timed, counted against a budget and
recorded as a ``ToolObservation``.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from udre.compiler.semantic_compiler import count_tokens
from udre.contracts.models import EdgeType, NodeType, ToolObservation
from udre.export.exporter import export_result
from udre.gateway.base import jsonable, normalize_text
from udre.gateway.registry import AccessContext, SourceGateway
from udre.memory.learning_store import LearningStore
from udre.ontology.builder import coll_id, col_id
from udre.ontology.embedding import tokens
from udre.ontology.graph import KnowledgeGraph

TOOL_NAMES = ["discover_sources", "discover_collections", "describe_collection", "inspect_column",
              "search_column_values", "sample_rows", "explore_relationships", "lookup_knowledge", "execute_query",
              "explain_query", "compare_results", "export_results",
              "list_skills", "load_skill", "list_collection_contexts", "load_collection_context", "execute_federated_query"]


class ToolBudgetExceeded(RuntimeError):
    pass


class ToolBox:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph, memory: Optional[LearningStore], exports_dir: str,
                 contexts: Any = None, skills: Any = None):
        self.gateway = gateway
        self.kg = kg
        self.memory = memory
        self.exports_dir = exports_dir
        self.contexts = contexts
        self.skills = skills

    # ------------------------------------------------------------------ skills (progressive disclosure)
    def list_skills(self, ctx: AccessContext) -> List[Dict[str, Any]]:
        return self.skills.list_skills() if self.skills else []

    def load_skill(self, ctx: AccessContext, skill_id: str) -> Dict[str, Any]:
        if not self.skills:
            raise KeyError("no skill registry configured")
        return self.skills.load_skill(skill_id)

    def list_collection_contexts(self, ctx: AccessContext, source_id: str) -> List[Dict[str, Any]]:
        if not self.contexts:
            return []
        return self.contexts.list_collection_contexts(ctx, source_id)

    def load_collection_context(self, ctx: AccessContext, source_id: str, collection: str,
                                include_value_profile: bool = False) -> Dict[str, Any]:
        if not self.contexts:
            raise KeyError("no collection context generator configured")
        return self.contexts.load_collection_context(ctx, source_id, collection, include_value_profile)

    def collection_catalog(self, ctx: AccessContext) -> List[Dict[str, Any]]:
        return self.contexts.catalog(ctx) if self.contexts else self.discover_collections(ctx)

    # ------------------------------------------------------------------ cross-source access without native joins
    def execute_federated_query(self, ctx: AccessContext, parts: Dict[str, Dict[str, Any]], combine_sql: str,
                                max_rows: int = 200) -> Dict[str, Any]:
        """Run bounded, permission-checked sub-queries on each source, then combine them locally.

        ``parts`` maps an alias to ``{"source_id": ..., "query": ...}``; ``combine_sql`` is a read-only
        DuckDB query over those aliases.  Each part is capped by the caller's row limit, so the combined
        result is only as complete as the parts (``truncated_parts`` reports any cap that was hit).
        """
        import duckdb
        import pandas as pd

        from udre.gateway.sql_guard import UnsafeQueryError, analyze, assert_read_only

        con = duckdb.connect(":memory:")
        provenance, truncated = {}, []
        try:
            for alias, spec in parts.items():
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
                    raise ValueError(f"invalid alias {alias}")
                res = self.gateway.execute_query(ctx, spec["source_id"], spec["query"], max_rows=ctx.max_rows)
                if not res.succeeded:
                    return {"succeeded": False, "error_message": f"part {alias}: {res.error_message}", "rows": []}
                if res.truncated:
                    truncated.append(alias)
                con.register("__part", pd.DataFrame(res.rows, columns=res.columns))
                con.execute(f'CREATE TABLE "{alias}" AS SELECT * FROM "__part"')
                con.unregister("__part")
                provenance[alias] = dict(res.provenance, source_id=spec["source_id"], rows=res.row_count)
            try:
                assert_read_only(combine_sql, "duckdb")
                unknown = analyze(combine_sql, "duckdb").tables - set(parts)
                if unknown:
                    raise UnsafeQueryError(f"combine query may only reference part aliases, got {sorted(unknown)}")
            except UnsafeQueryError as exc:
                return {"succeeded": False, "error_message": f"REJECTED: {exc}", "rows": []}
            cur = con.execute(combine_sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(max_rows + 1)
            return {"succeeded": True, "columns": cols, "rows": [dict(zip(cols, map(jsonable, r))) for r in rows[:max_rows]],
                    "row_count": min(len(rows), max_rows), "truncated": len(rows) > max_rows, "truncated_parts": truncated,
                    "provenance": provenance}
        finally:
            con.close()

    # ------------------------------------------------------------------ 1
    def discover_sources(self, ctx: AccessContext) -> List[Dict[str, Any]]:
        return [{"source_id": s.source_id, "name": s.name, "type": s.source_type.value, "dialect":
                 self.gateway.adapter(ctx, s.source_id).dialect, "collections": len(self.gateway.collections(ctx, s.source_id)),
                 "read_only": s.capabilities.read_only, "max_rows": s.capabilities.max_rows_limit}
                for s in self.gateway.sources(ctx)]

    # ------------------------------------------------------------------ 2
    def discover_collections(self, ctx: AccessContext, query: Optional[str] = None, source_id: Optional[str] = None,
                             k: int = 8) -> List[Dict[str, Any]]:
        """Skill retrieval: vector similarity on collection descriptors, re-ranked lexically."""
        allowed = {(s.source_id, c) for s in self.gateway.sources(ctx) for c in self.gateway.collections(ctx, s.source_id)}
        if not query:
            return [self._skill(n) for n in self.kg.nodes(NodeType.COLLECTION, source_id, ctx.tenant_id)
                    if (n["source_id"], n["label"]) in allowed]
        hits = self.kg.search(query, [NodeType.COLLECTION], k=max(k * 3, 20), source_id=source_id, tenant=ctx.tenant_id)
        qt = set(tokens(query))
        scored = []
        for node, score in hits:
            if (node["source_id"], node["label"]) not in allowed:
                continue
            name_t = set(tokens(node["label"].split(".")[-1]))
            col_t = set(t for c in node["properties"].get("columns", []) for t in tokens(c))
            lexical = 0.25 * len(qt & name_t) + 0.05 * len(qt & col_t)
            scored.append((score + lexical, node))
        scored.sort(key=lambda x: -x[0])
        return [dict(self._skill(n), relevance=round(s, 3)) for s, n in scored[:k]]

    @staticmethod
    def _skill(node: Dict[str, Any]) -> Dict[str, Any]:
        skill = node["properties"].get("skill") or {}
        return {"source_id": node["source_id"], "collection": node["label"], "summary": skill.get("summary", ""),
                "rows": node["properties"].get("row_count"), "metrics": skill.get("metric_columns", []),
                "dimensions": skill.get("dimension_columns", []), "keys": skill.get("key_columns", []),
                "joinable_with": skill.get("joinable_with", [])}

    # ------------------------------------------------------------------ 3
    def describe_collection(self, ctx: AccessContext, source_id: str, collection: str) -> Dict[str, Any]:
        self.gateway.check_collection(ctx, source_id, collection)
        schema = self.gateway.adapter(ctx, source_id).describe_collection(collection)
        col_nodes = {n["properties"]["column"]: n["properties"] for n in self.kg.nodes(NodeType.COLUMN, source_id, ctx.tenant_id)
                     if n["properties"].get("collection") == collection}
        cols = []
        for c in schema.columns:
            p = col_nodes.get(c.name, {})
            cols.append({"name": c.name, "type": c.data_type, "description": c.description, "pk": c.is_primary_key,
                         "fk": c.foreign_key_target, "nullable": c.nullable, "measure": p.get("is_measure"),
                         "dimension": p.get("is_dimension"), "null_ratio": p.get("null_ratio"),
                         "distinct": p.get("distinct_count"), "top_values": [t["value"] for t in (p.get("top_values") or [])][:12],
                         "min": p.get("min"), "max": p.get("max")})
        return {"source_id": source_id, "collection": collection, "description": schema.description,
                "row_count": schema.row_count, "primary_keys": schema.primary_keys, "columns": cols}

    # ------------------------------------------------------------------ 4
    def inspect_column(self, ctx: AccessContext, source_id: str, collection: str, column: str) -> Dict[str, Any]:
        self.gateway.check_collection(ctx, source_id, collection)
        return self.gateway.adapter(ctx, source_id).profile_column(collection, column).model_dump()

    # ------------------------------------------------------------------ 5
    def search_column_values(self, ctx: AccessContext, source_id: str, term: str, collection: Optional[str] = None,
                             column: Optional[str] = None, k: int = 8) -> List[Dict[str, Any]]:
        """Exact/fuzzy value matching against real data. Without a column, candidate columns come from
        the value-mapping graph and every candidate is re-checked live against the source."""
        adapter = self.gateway.adapter(ctx, source_id)
        targets: List[Tuple[str, str]] = []
        if collection and column:
            targets = [(collection, column)]
        else:
            for node, score in self.kg.search(term, [NodeType.VALUE_MAPPING], k=40, source_id=source_id, tenant=ctx.tenant_id):
                p = node["properties"]
                if collection and p["collection"] != collection:
                    continue
                if (p["collection"], p["column"]) not in targets and score > 0.3:
                    targets.append((p["collection"], p["column"]))
            if collection and not targets:
                schema = adapter.describe_collection(collection)
                cats = {n["properties"]["column"] for n in self.kg.nodes(NodeType.COLUMN, source_id, ctx.tenant_id)
                        if n["properties"].get("collection") == collection and n["properties"].get("is_dimension")}
                targets = [(collection, c.name) for c in schema.columns if c.name in cats]
        out = []
        for coll, col in targets[:12]:
            if f"{source_id}:{coll}" in ctx.denied_collections:
                continue
            for m in adapter.search_values(coll, col, term, limit=3):
                out.append(m.model_dump())
        out.sort(key=lambda m: (-m["similarity_score"], -m["occurrence_count"]))
        return out[:k]

    # ------------------------------------------------------------------ 6
    def sample_rows(self, ctx: AccessContext, source_id: str, collection: str, n: int = 5) -> Dict[str, Any]:
        self.gateway.check_collection(ctx, source_id, collection)
        return self.gateway.adapter(ctx, source_id).sample_rows(collection, min(n, 20)).model_dump()

    # ------------------------------------------------------------------ 7
    def explore_relationships(self, ctx: AccessContext, source_id: str, collection: str) -> Dict[str, Any]:
        refs = self.kg.edges(EdgeType.REFERENCES, source_id=source_id, tenant=ctx.tenant_id)
        rules = self.kg.edges(EdgeType.DERIVED_FROM, source_id=source_id, tenant=ctx.tenant_id)
        out_rel, in_rel = [], []
        for e in refs:
            p = e["properties"]
            item = {"from": f"{p['child']}.{p['child_col']}", "to": f"{p['parent']}.{p['parent_col']}", "fanout": p.get("fanout"),
                    "null_keys": p.get("null_keys"), "status": e["verification_status"], "declared": p.get("declared")}
            if p["child"] == collection:
                out_rel.append(item)
            elif p["parent"] == collection:
                in_rel.append(item)
        recon = [e["properties"] for e in rules if e["properties"].get("rule")
                 and collection in (e["properties"]["parent"], e["properties"]["child"])]
        return {"collection": collection, "references_parents": out_rel, "referenced_by_children": in_rel,
                "reconciliation_rules": recon}

    def relationship_graph(self, ctx: AccessContext, source_id: str) -> List[Dict[str, Any]]:
        return [dict(e["properties"], status=e["verification_status"])
                for e in self.kg.edges(EdgeType.REFERENCES, source_id=source_id, tenant=ctx.tenant_id)
                if e["verification_status"] != "stale_schema"]

    def join_path(self, ctx: AccessContext, source_id: str, start: str, goal: str, many_to_one_only: bool = True,
                  max_hops: int = 4) -> Optional[List[Dict[str, Any]]]:
        """BFS over verified references. By default only child->parent (N:1) hops, which cannot fan out."""
        if start == goal:
            return []
        rels = self.relationship_graph(ctx, source_id)
        adj: Dict[str, List[Tuple[str, Dict[str, Any], str]]] = {}
        for r in rels:
            adj.setdefault(r["child"], []).append((r["parent"], r, "n:1"))
            if not many_to_one_only:
                adj.setdefault(r["parent"], []).append((r["child"], r, "1:n"))
        queue = deque([(start, [])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            if len(path) >= max_hops:
                continue
            for nxt, rel, direction in sorted(adj.get(node, []), key=lambda x: (x[0], x[1]["child_col"])):
                if nxt in seen:
                    continue
                step = dict(rel, direction=direction, left=node, right=nxt)
                if nxt == goal:
                    return path + [step]
                seen.add(nxt)
                queue.append((nxt, path + [step]))
        return None

    # ------------------------------------------------------------------ 8
    def lookup_knowledge(self, ctx: AccessContext, query: str, k: int = 8) -> Dict[str, Any]:
        hits = self.kg.search(query, [NodeType.CONCEPT, NodeType.METRIC, NodeType.COLUMN], k=k, tenant=ctx.tenant_id)
        knowledge = [{"id": n["node_id"], "type": n["node_type"], "label": n["label"], "status": n["verification_status"],
                      "score": round(s, 3), "definition": n["properties"].get("definition_sql")} for n, s in hits]
        patterns, mappings = [], {}
        if self.memory:
            versions = self.source_versions(ctx)
            patterns = self.memory.similar(ctx, query, "query_template", versions, k=3, min_score=0.6)
            mappings = self.memory.value_mappings(ctx, versions)
        return {"knowledge": knowledge, "verified_query_patterns": patterns, "learned_value_mappings": mappings}

    def source_versions(self, ctx: AccessContext) -> Dict[str, str]:
        out = {}
        for s in self.gateway.sources(ctx):
            v = self.kg.source_version(s.source_id, ctx.tenant_id)
            if v:
                out[s.source_id] = v
        return out

    # ------------------------------------------------------------------ 9 / 10
    def execute_query(self, ctx: AccessContext, source_id: str, query: str, max_rows: int = 200) -> Dict[str, Any]:
        return self.gateway.execute_query(ctx, source_id, query, max_rows=max_rows).model_dump()

    def explain_query(self, ctx: AccessContext, source_id: str, query: str) -> Dict[str, Any]:
        return {"plan": self.gateway.explain_query(ctx, source_id, query)}

    # ------------------------------------------------------------------ 11
    @staticmethod
    def compare_results(a: Sequence[Dict[str, Any]], b: Sequence[Dict[str, Any]], rel_tol: float = 0.001,
                        abs_tol: float = 0.01) -> Dict[str, Any]:
        """Compare two result sets on their numeric value columns (keys = remaining columns)."""
        def index(rows):
            out = {}
            for r in rows:
                nums = [v for v in r.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
                keys = tuple(normalize_text(v) for v in r.values() if not (isinstance(v, (int, float)) and not isinstance(v, bool)))
                out[keys] = nums[-1] if nums else None
            return out

        ia, ib = index(a), index(b)
        diffs = []
        if len(ia) == 1 and len(ib) == 1:
            ia, ib = {(): next(iter(ia.values()))}, {(): next(iter(ib.values()))}
        for key in sorted(set(ia) | set(ib), key=str):
            va, vb = ia.get(key), ib.get(key)
            same = va is not None and vb is not None and abs(va - vb) <= max(abs_tol, rel_tol * max(abs(va), abs(vb)))
            if not same:
                diffs.append({"key": list(key), "a": va, "b": vb})
        return {"equivalent": not diffs and bool(ia), "differences": diffs[:20], "rows_a": len(a), "rows_b": len(b)}

    # ------------------------------------------------------------------ 12
    def export_results(self, rows: List[Dict[str, Any]], fmt: str, name: str = "result",
                       metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"path": export_result(rows, fmt, self.exports_dir, name, metadata), "format": fmt, "rows": len(rows)}


class ToolSession:
    """Per-agent binding of the toolbox: authorization context, budget and observation log."""

    def __init__(self, toolbox: ToolBox, ctx: AccessContext, agent: str, max_calls: int = 40):
        self.toolbox = toolbox
        self.ctx = ctx
        self.agent = agent
        self.max_calls = max_calls
        self.observations: List[ToolObservation] = []
        self.tokens_observed = 0

    def call(self, tool: str, objective: str, phase: str, **kwargs: Any) -> Any:
        if tool not in TOOL_NAMES and tool not in ("join_path", "relationship_graph", "source_versions", "collection_catalog"):
            raise ValueError(f"unknown tool {tool}")
        if not objective:
            raise ValueError("every tool call must state its investigation objective")
        if len(self.observations) >= self.max_calls:
            raise ToolBudgetExceeded(f"{self.agent} exceeded {self.max_calls} tool calls")
        fn: Callable[..., Any] = getattr(self.toolbox, tool)
        t0 = time.perf_counter()
        if tool in ("compare_results", "export_results"):
            result = fn(**kwargs)
        else:
            result = fn(self.ctx, **kwargs)
        latency = (time.perf_counter() - t0) * 1000
        preview = json.dumps(result, default=str)[:4000]
        self.tokens_observed += count_tokens(preview)
        self.observations.append(ToolObservation(
            step_index=len(self.observations), phase=phase, objective=objective, tool_name=tool,
            tool_args={k: (v if not isinstance(v, (list, dict)) else f"<{type(v).__name__}>") for k, v in kwargs.items()},
            observation_summary=_summarize(tool, result), raw_output_preview=preview[:600], latency_ms=round(latency, 2)))
        return result


def _summarize(tool: str, result: Any) -> str:
    if isinstance(result, dict) and "succeeded" in result:
        return (f"ok rows={result.get('row_count')} truncated={result.get('truncated')}" if result["succeeded"]
                else f"error: {result.get('error_message')}")
    if isinstance(result, list):
        return f"{len(result)} items" + (f"; top={json.dumps(result[0], default=str)[:160]}" if result else "")
    if isinstance(result, dict):
        return ", ".join(f"{k}={str(v)[:60]}" for k, v in list(result.items())[:4])
    return str(result)[:200]
