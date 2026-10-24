"""Cross-source relationship candidates (e.g. CRM company <-> finance customer).

Candidates come from exact overlap of *normalized* values in identifier-like text columns of collections
in different sources.  They are always stored as ``inferred_candidate`` MAPS_TO edges with the overlap
evidence; the engine never promotes them to identity automatically, and name overlap is labelled as
such ("normalized name overlap only").
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.contracts.models import EdgeType, EvidenceReference, GraphEdge, NodeType, VerificationStatus
from backend.gateway.registry import AccessContext, SourceGateway
from backend.ontology.embedding import tokens
from backend.ontology.graph import KnowledgeGraph

ID_HINT = {"name", "domain", "email", "title", "label", "legal", "company", "account", "customer", "conglomerate", "counterparty"}
SUFFIXES = r"\b(inc|incorporated|ltd|limited|llc|plc|gmbh|bv|b\.v|pte|corp|corporation|co|company|holdings|group|sa|ag)\b\.?"


def normalize_identity(value: Any) -> str:
    s = str(value or "").lower().strip()
    s = re.sub(SUFFIXES, " ", s)
    s = re.sub(r"[^a-z0-9@.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class CrossSourceMatcher:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph, min_matches: int = 3, min_ratio: float = 0.2,
                 max_values: int = 5000):
        self.gateway = gateway
        self.kg = kg
        self.min_matches = min_matches
        self.min_ratio = min_ratio
        self.max_values = max_values

    def _candidate_columns(self, ctx: AccessContext, source_id: str) -> List[Tuple[str, str]]:
        allowed = set(self.gateway.collections(ctx, source_id))
        out = []
        for n in self.kg.nodes(NodeType.COLUMN, source_id, ctx.tenant_id):
            p = n["properties"]
            if n["verification_status"] == "stale_schema" or p.get("collection") not in allowed:
                continue
            if p.get("family") != "text" or (p.get("distinct_count") or 0) < self.min_matches or p.get("is_primary_key"):
                continue
            if set(tokens(p["column"])) & ID_HINT and (p.get("cardinality_ratio") or 0) > 0.3:
                out.append((p["collection"], p["column"]))
        return out

    def _values(self, ctx: AccessContext, source_id: str, coll: str, col: str) -> Dict[str, str]:
        adapter = self.gateway.adapter(ctx, source_id)
        q = adapter.quote
        res = self.gateway.execute_query(ctx, source_id, f"SELECT DISTINCT CAST({q(col)} AS VARCHAR) AS v FROM {q(coll)} "
                                                         f"WHERE {q(col)} IS NOT NULL", max_rows=self.max_values)
        out = {}
        for r in res.rows if res.succeeded else []:
            nv = normalize_identity(r["v"])
            if len(nv) >= 3:
                out.setdefault(nv, r["v"])
        return out

    def discover(self, ctx: AccessContext, focus_source: Optional[str] = None) -> List[Dict[str, Any]]:
        sources = [s.source_id for s in self.gateway.sources(ctx)]
        cols = {sid: self._candidate_columns(ctx, sid) for sid in sources}
        cache: Dict[Tuple[str, str, str], Dict[str, str]] = {}

        def vals(sid, coll, col):
            key = (sid, coll, col)
            if key not in cache:
                cache[key] = self._values(ctx, sid, coll, col)
            return cache[key]

        found, edges, evidence = [], [], []
        for i, left in enumerate(sources):
            for right in sources[i + 1:]:
                if focus_source and focus_source not in (left, right):
                    continue
                for lc, lcol in cols[left]:
                    lv = vals(left, lc, lcol)
                    for rc, rcol in cols[right]:
                        rv = vals(right, rc, rcol)
                        common = set(lv) & set(rv)
                        smaller = min(len(lv), len(rv)) or 1
                        if len(common) < self.min_matches or len(common) / smaller < self.min_ratio:
                            continue
                        basis = "normalized name overlap only" if not ({"domain", "email"} & set(tokens(lcol + " " + rcol))) \
                            else "normalized identifier overlap"
                        eid = "ev:xsrc:" + hashlib.sha1(f"{left}|{lc}.{lcol}|{right}|{rc}.{rcol}".encode()).hexdigest()[:16]
                        sample = sorted(common)[:8]
                        evidence.append(EvidenceReference(
                            evidence_id=eid, source_id=left, collection_name=lc, column_name=lcol, evidence_type="query_result",
                            query_or_action=f"distinct normalized values {left}:{lc}.{lcol} vs {right}:{rc}.{rcol}",
                            observed_summary=f"{len(common)} common values of {len(lv)} / {len(rv)}",
                            sample_data=[{"left": lv[v], "right": rv[v]} for v in sample]))
                        props = {"kind": "cross_source_candidate", "left_source": left, "left_collection": lc, "left_column": lcol,
                                 "right_source": right, "right_collection": rc, "right_column": rcol, "matches": len(common),
                                 "ratio": round(len(common) / smaller, 3), "basis": basis,
                                 "note": "candidate only; matching values do not establish entity identity"}
                        edges.append(GraphEdge(
                            edge_id=f"e:xsrc:{left}:{lc}.{lcol}~{right}:{rc}.{rcol}",
                            source_node_id=f"col:{left}:{lc}.{lcol}", target_node_id=f"col:{right}:{rc}.{rcol}",
                            edge_type=EdgeType.MAPS_TO, properties=props, provenance="cross_source_value_overlap",
                            verification_status=VerificationStatus.INFERRED_CANDIDATE, schema_version="cross_source",
                            confidence=round(min(0.6 if "name" in basis else 0.8, len(common) / smaller), 3), evidence_refs=[eid]))
                        found.append(props)
        self.kg.add_evidence(evidence, ctx.tenant_id)
        self.kg.upsert_edges(edges, ctx.tenant_id, source_id="cross_source")
        return found
