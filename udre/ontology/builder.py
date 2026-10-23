"""Generic ontology builder: physical discovery -> profiling -> relationships -> semantics.

Nothing here knows about specific tables. Every source is processed with the same steps:
  1. discover collections, describe schemas, profile every column
  2. physical graph (Source -CONTAINS-> Collection -HAS_COLUMN-> Column)
  3. relationships: declared FKs (verified by catalog + orphan check) and inferred FKs
     (name + uniqueness + value containment, verified only when containment is total)
  4. reconciliation rules: parent numeric column == SUM(child numeric column) per key,
     discovered and verified by executing the check (DERIVED_FROM)
  5. semantic layer from terminology packs: Concept / Metric / Dimension / ValueMapping nodes
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from udre.contracts.models import (
    CollectionSchema,
    CollectionSkillDescriptor,
    ColumnProfile,
    EdgeType,
    EvidenceReference,
    GraphEdge,
    GraphNode,
    NodeType,
    VerificationStatus,
)
from udre.gateway.base import DataSourceAdapter, normalize_text, type_family
from udre.gateway.registry import AccessContext, SourceGateway
from udre.ontology.embedding import tokens
from udre.ontology.graph import KnowledgeGraph

PACK_DIR = Path(__file__).resolve().parents[1] / "domain_packs"
GENERIC_TOKENS = {"id", "code", "date", "name", "the", "of", "in", "for", "and", "a", "to", "at", "is", "on", "by", "per"}
V, I = VerificationStatus.VERIFIED_FACT, VerificationStatus.INFERRED_CANDIDATE


def load_packs(names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    packs = []
    for p in sorted(PACK_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if names is None or data["name"] in names:
            packs.append(data)
    return packs


def col_id(source_id: str, collection: str, column: str) -> str:
    return f"col:{source_id}:{collection}.{column}"


def coll_id(source_id: str, collection: str) -> str:
    return f"coll:{source_id}:{collection}"


def _eid(*parts: str) -> str:
    return "ev:" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def is_measure(col_name: str, profile: ColumnProfile, is_key: bool) -> bool:
    if is_key or type_family(profile.data_type) != "numeric":
        return False
    n = col_name.lower()
    if n.endswith(("_id", "_seq", "_number", "_ordinal", "_level", "_depth", "_year")) or n in ("line_number", "page_number"):
        return False
    return True


def is_dimension(profile: ColumnProfile) -> bool:
    return profile.is_categorical and profile.distinct_count >= 1


def match_concepts(text_name: str, text_desc: str, packs: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """Lexical concept matching on column/collection names (strong) and descriptions (weak)."""
    name_toks = " " + " ".join(tokens(text_name)) + " "
    desc_toks = " " + " ".join(tokens(text_desc)) + " "
    scores: Dict[str, float] = {}
    for pack in packs:
        for concept, spec in pack["concepts"].items():
            best = 0.0
            for syn in spec["synonyms"]:
                s = " " + " ".join(tokens(syn)) + " "
                if s in name_toks:
                    best = max(best, 1.0)
                elif s in desc_toks:
                    best = max(best, 0.6)
            if best:
                scores[concept] = best
    return sorted(scores.items(), key=lambda kv: -kv[1])


@dataclass
class BuildReport:
    source_id: str
    schema_version: str
    skipped: bool = False
    collections: int = 0
    columns: int = 0
    declared_relationships: int = 0
    inferred_relationships: int = 0
    verified_relationships: int = 0
    reconciliation_rules: int = 0
    concepts_linked: int = 0
    metrics: int = 0
    value_mappings: int = 0
    stale_marked: Dict[str, int] = field(default_factory=dict)


class OntologyBuilder:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph, packs: Optional[List[Dict[str, Any]]] = None,
                 max_workers: int = 8):
        self.gateway = gateway
        self.kg = kg
        self.packs = packs if packs is not None else load_packs()
        self.max_workers = max_workers

    # ------------------------------------------------------------------ entry point
    def build(self, ctx: AccessContext, source_id: str, force: bool = False) -> BuildReport:
        adapter = self.gateway.adapter(ctx, source_id)
        version = adapter.schema_fingerprint()
        report = BuildReport(source_id=source_id, schema_version=version)
        if not force and self.kg.source_version(source_id, ctx.tenant_id) == version:
            report.skipped = True
            return report
        report.stale_marked = self.kg.invalidate_source(source_id, version, ctx.tenant_id)
        schemas = {c: adapter.describe_collection(c) for c in self.gateway.collections(ctx, source_id)}
        profiles = self._profile_all(adapter, schemas)
        self._physical(ctx, adapter, schemas, profiles, version, report)
        rels = self._relationships(ctx, adapter, schemas, profiles, version, report)
        self._reconciliation_rules(ctx, adapter, schemas, profiles, rels, version, report)
        self._semantics(ctx, adapter, schemas, profiles, rels, version, report)
        return report

    # ------------------------------------------------------------------ profiling
    def _profile_all(self, adapter: DataSourceAdapter, schemas: Dict[str, CollectionSchema]) -> Dict[Tuple[str, str], ColumnProfile]:
        if hasattr(adapter, "profile_collection"):
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                per_coll = list(pool.map(lambda c: (c, adapter.profile_collection(c)), list(schemas)))
            return {(c, col): p for c, profs in per_coll for col, p in profs.items()}
        jobs = [(c, col.name) for c, s in schemas.items() for col in s.columns]
        return {j: adapter.profile_column(j[0], j[1]) for j in jobs}

    # ------------------------------------------------------------------ physical layer
    def _physical(self, ctx, adapter, schemas, profiles, version, report: BuildReport) -> None:
        sid, tenant = adapter.source_id, ctx.tenant_id
        catalog_ev = EvidenceReference(
            evidence_id=_eid("catalog", sid, version), source_id=sid, evidence_type="schema_constraint",
            query_or_action="catalog discovery (information_schema / file metadata)",
            observed_summary=f"{len(schemas)} collections, {sum(len(s.columns) for s in schemas.values())} columns")
        evidence = [catalog_ev]
        nodes = [GraphNode(node_id=f"src:{sid}", node_type=NodeType.SOURCE, label=adapter.descriptor.name, source_id=sid,
                           properties={"source_type": adapter.descriptor.source_type.value, "dialect": adapter.dialect,
                                       "description": adapter.descriptor.description},
                           provenance="registration", verification_status=V, schema_version=version, confidence=1.0,
                           evidence_refs=[catalog_ev.evidence_id])]
        edges: List[GraphEdge] = []
        for cname, schema in schemas.items():
            cid = coll_id(sid, cname)
            prof_ev = EvidenceReference(
                evidence_id=_eid("profile", sid, cname, version), source_id=sid, collection_name=cname,
                evidence_type="column_profile", query_or_action=f"profile all columns of {cname}",
                observed_summary=f"{schema.row_count} rows; " + "; ".join(
                    f"{c.name}: distinct={profiles[(cname, c.name)].distinct_count} nulls={profiles[(cname, c.name)].null_ratio}"
                    for c in schema.columns[:12]))
            evidence.append(prof_ev)
            nodes.append(GraphNode(
                node_id=cid, node_type=NodeType.COLLECTION, label=cname, source_id=sid,
                properties={"description": schema.description, "row_count": schema.row_count,
                            "primary_keys": schema.primary_keys, "schema_name": schema.schema_name,
                            "columns": [c.name for c in schema.columns]},
                provenance="catalog", verification_status=V, schema_version=version, confidence=1.0,
                evidence_refs=[catalog_ev.evidence_id, prof_ev.evidence_id]))
            edges.append(GraphEdge(edge_id=f"e:contains:{cid}", source_node_id=f"src:{sid}", target_node_id=cid,
                                   edge_type=EdgeType.CONTAINS, provenance="catalog", verification_status=V,
                                   schema_version=version, confidence=1.0, evidence_refs=[catalog_ev.evidence_id]))
            for col in schema.columns:
                p = profiles[(cname, col.name)]
                is_key = col.is_primary_key or col.is_foreign_key or p.cardinality_ratio > 0.98 and col.name.endswith("id")
                nodes.append(GraphNode(
                    node_id=col_id(sid, cname, col.name), node_type=NodeType.COLUMN, label=f"{cname}.{col.name}", source_id=sid,
                    properties={"collection": cname, "column": col.name, "data_type": col.data_type,
                                "family": type_family(col.data_type), "description": col.description,
                                "nullable": col.nullable, "is_primary_key": col.is_primary_key,
                                "foreign_key_target": col.foreign_key_target, "is_key": bool(is_key),
                                "is_measure": is_measure(col.name, p, bool(is_key)), "is_dimension": is_dimension(p),
                                "null_ratio": p.null_ratio, "distinct_count": p.distinct_count,
                                "cardinality_ratio": p.cardinality_ratio, "min": p.min_value, "max": p.max_value,
                                "mean": p.mean_value, "top_values": p.top_values[:15], "samples": p.sample_values[:3]},
                    provenance="catalog+profile", verification_status=V, schema_version=version, confidence=1.0,
                    evidence_refs=[prof_ev.evidence_id]),
                )
                edges.append(GraphEdge(edge_id=f"e:has:{col_id(sid, cname, col.name)}", source_node_id=cid,
                                       target_node_id=col_id(sid, cname, col.name), edge_type=EdgeType.HAS_COLUMN,
                                       provenance="catalog", verification_status=V, schema_version=version,
                                       confidence=1.0, evidence_refs=[catalog_ev.evidence_id]))
                report.columns += 1
            report.collections += 1
        self.kg.add_evidence(evidence, tenant)
        embed = {n.node_id: f"{n.label} {n.properties.get('description', '')} {' '.join(n.properties.get('columns', []))}"
                 for n in nodes}
        self.kg.upsert_nodes(nodes, tenant, embed)
        self.kg.upsert_edges(edges, tenant, source_id=sid)

    # ------------------------------------------------------------------ relationships
    def _relationships(self, ctx, adapter, schemas, profiles, version, report: BuildReport) -> List[Dict[str, Any]]:
        sid, tenant = adapter.source_id, ctx.tenant_id
        rels: List[Dict[str, Any]] = []
        declared = set()
        for cname, schema in schemas.items():
            for fk in schema.foreign_keys:
                target_coll, target_col = fk["references"].rsplit(".", 1)
                if target_coll in schemas:
                    rels.append(dict(child=cname, child_col=fk["column"], parent=target_coll, parent_col=target_col,
                                     declared=True))
                    declared.add((cname, fk["column"]))
        unique_cols = {(c, col.name) for c, s in schemas.items() for col in s.columns
                       if profiles[(c, col.name)].total_count > 0
                       and profiles[(c, col.name)].distinct_count == profiles[(c, col.name)].total_count
                       and type_family(col.data_type) in ("text", "numeric")}
        for cname, schema in schemas.items():
            for col in schema.columns:
                if (cname, col.name) in declared or (cname, col.name) in unique_cols or not col.name.lower().endswith(("id", "code", "key")):
                    continue
                for (pc, pcol) in unique_cols:
                    if pc != cname and pcol == col.name:
                        rels.append(dict(child=cname, child_col=col.name, parent=pc, parent_col=pcol, declared=False))
        edges, evidence = [], []
        q = adapter.quote if hasattr(adapter, "quote") else (lambda n: n)
        for r in rels:
            sql = (f"SELECT COUNT(*) AS child_rows, COUNT(c.{q(r['child_col'])}) AS non_null, "
                   f"COUNT(DISTINCT c.{q(r['child_col'])}) AS distinct_keys, "
                   f"SUM(CASE WHEN c.{q(r['child_col'])} IS NOT NULL AND p.{q(r['parent_col'])} IS NULL THEN 1 ELSE 0 END) AS orphans "
                   f"FROM {q(r['child'])} c LEFT JOIN {q(r['parent'])} p ON c.{q(r['child_col'])} = p.{q(r['parent_col'])}")
            res = adapter.execute(sql, max_rows=1)
            if not res.succeeded:
                continue
            row = res.rows[0]
            non_null, orphans, distinct = int(row["non_null"] or 0), int(row["orphans"] or 0), int(row["distinct_keys"] or 0)
            containment = 1 - orphans / non_null if non_null else 0.0
            fanout = round(non_null / distinct, 3) if distinct else 0.0
            r.update(containment=round(containment, 4), fanout=fanout, child_rows=int(row["child_rows"]),
                     null_keys=int(row["child_rows"]) - non_null, cardinality="many_to_one" if fanout > 1.0 else "one_to_one")
            if not r["declared"] and containment < 0.9:
                continue
            ev = EvidenceReference(
                evidence_id=_eid("rel", sid, r["child"], r["child_col"], r["parent"], version), source_id=sid,
                collection_name=r["child"], column_name=r["child_col"],
                evidence_type="schema_constraint" if r["declared"] else "query_result", query_or_action=sql,
                observed_summary=f"containment={r['containment']} orphans={orphans} fanout={fanout} null_keys={r['null_keys']}",
                sample_data=[row])
            evidence.append(ev)
            verified = (r["declared"] and orphans == 0) or (not r["declared"] and orphans == 0 and non_null > 0)
            r["verified"] = verified
            edges.append(GraphEdge(
                edge_id=f"e:ref:{sid}:{r['child']}.{r['child_col']}->{r['parent']}.{r['parent_col']}",
                source_node_id=col_id(sid, r["child"], r["child_col"]), target_node_id=col_id(sid, r["parent"], r["parent_col"]),
                edge_type=EdgeType.REFERENCES,
                properties={k: r[k] for k in ("child", "child_col", "parent", "parent_col", "declared", "containment",
                                              "fanout", "null_keys", "cardinality")},
                provenance="declared_foreign_key" if r["declared"] else "inferred_name_and_containment",
                verification_status=V if verified else I, schema_version=version,
                confidence=1.0 if verified else round(0.5 + 0.4 * containment, 3), evidence_refs=[ev.evidence_id]))
            report.declared_relationships += r["declared"]
            report.inferred_relationships += not r["declared"]
            report.verified_relationships += verified
        self.kg.add_evidence(evidence, tenant)
        self.kg.upsert_edges(edges, tenant, source_id=sid)
        return [r for r in rels if "verified" in r]

    # ------------------------------------------------------------------ reconciliation rules
    def _reconciliation_rules(self, ctx, adapter, schemas, profiles, rels, version, report: BuildReport) -> None:
        sid, tenant = adapter.source_id, ctx.tenant_id
        q = adapter.quote
        edges, evidence = [], []
        for r in rels:
            pmeasures = [c.name for c in schemas[r["parent"]].columns
                         if is_measure(c.name, profiles[(r["parent"], c.name)], c.is_primary_key or c.is_foreign_key)]
            cmeasures = [c.name for c in schemas[r["child"]].columns
                         if is_measure(c.name, profiles[(r["child"], c.name)], c.is_primary_key or c.is_foreign_key)]
            for pm in pmeasures:
                ptok = set(tokens(pm)) - GENERIC_TOKENS
                for cm in cmeasures:
                    if not (ptok & (set(tokens(cm)) - GENERIC_TOKENS)):
                        continue
                    sql = (f"SELECT COUNT(*) AS compared, SUM(CASE WHEN ABS(p.{q(pm)} - s.total) > 0.005 THEN 1 ELSE 0 END) AS mismatches "
                           f"FROM {q(r['parent'])} p JOIN (SELECT {q(r['child_col'])} AS k, SUM({q(cm)}) AS total "
                           f"FROM {q(r['child'])} GROUP BY {q(r['child_col'])}) s ON s.k = p.{q(r['parent_col'])}")
                    res = adapter.execute(sql, max_rows=1)
                    if not res.succeeded or not res.rows or not res.rows[0]["compared"]:
                        continue
                    compared, mism = int(res.rows[0]["compared"]), int(res.rows[0]["mismatches"] or 0)
                    if mism:
                        continue
                    ev = EvidenceReference(
                        evidence_id=_eid("recon", sid, r["parent"], pm, r["child"], cm, version), source_id=sid,
                        collection_name=r["parent"], column_name=pm, evidence_type="reconciliation_check",
                        query_or_action=sql, observed_summary=f"{r['parent']}.{pm} == SUM({r['child']}.{cm}) for all {compared} keys",
                        sample_data=res.rows)
                    evidence.append(ev)
                    edges.append(GraphEdge(
                        edge_id=f"e:derived:{sid}:{r['parent']}.{pm}<-{r['child']}.{cm}",
                        source_node_id=col_id(sid, r["parent"], pm), target_node_id=col_id(sid, r["child"], cm),
                        edge_type=EdgeType.DERIVED_FROM,
                        properties={"rule": "parent_equals_sum_of_child", "aggregation": "SUM", "parent": r["parent"],
                                    "parent_col": pm, "child": r["child"], "child_col": cm, "join_parent": r["parent_col"],
                                    "join_child": r["child_col"], "keys_compared": compared},
                        provenance="discovered_reconciliation", verification_status=V, schema_version=version,
                        confidence=1.0, evidence_refs=[ev.evidence_id]))
                    report.reconciliation_rules += 1
        self.kg.add_evidence(evidence, tenant)
        self.kg.upsert_edges(edges, tenant, source_id=sid)

    # ------------------------------------------------------------------ semantic layer
    def _semantics(self, ctx, adapter, schemas, profiles, rels, version, report: BuildReport) -> None:
        sid, tenant = adapter.source_id, ctx.tenant_id
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []
        evidence: List[EvidenceReference] = []
        embed: Dict[str, str] = {}
        for pack in self.packs:
            for concept, spec in pack["concepts"].items():
                nid = f"concept:{pack['name']}:{concept}"
                nodes.append(GraphNode(node_id=nid, node_type=NodeType.CONCEPT, label=concept,
                                       properties={"kind": spec["kind"], "synonyms": spec["synonyms"], "pack": pack["name"]},
                                       provenance=f"terminology_pack:{pack['name']}", verification_status=I,
                                       schema_version="pack", confidence=0.9))
                embed[nid] = f"{concept.replace('_', ' ')} {' '.join(spec['synonyms'])}"
        joinable: Dict[str, set] = {}
        for r in rels:
            joinable.setdefault(r["child"], set()).add(r["parent"])
            joinable.setdefault(r["parent"], set()).add(r["child"])
        col_concepts: Dict[str, List[Tuple[str, float]]] = {}
        for cname, schema in schemas.items():
            coll_concepts = match_concepts(cname.split(".")[-1], schema.description, self.packs)
            for concept, score in coll_concepts[:4]:
                pack = next(p["name"] for p in self.packs if concept in p["concepts"])
                edges.append(GraphEdge(edge_id=f"e:rep:{coll_id(sid, cname)}->{concept}", source_node_id=coll_id(sid, cname),
                                       target_node_id=f"concept:{pack}:{concept}", edge_type=EdgeType.REPRESENTS_CONCEPT,
                                       properties={"match_score": score}, provenance="lexical_match",
                                       verification_status=I, schema_version=version, confidence=round(0.5 * score + 0.3, 3)))
            measures, dims = [], []
            for col in schema.columns:
                p = profiles[(cname, col.name)]
                cid = col_id(sid, cname, col.name)
                matches = match_concepts(col.name, col.description, self.packs)
                col_concepts[cid] = matches
                for concept, score in matches[:3]:
                    pack = next(pk["name"] for pk in self.packs if concept in pk["concepts"])
                    edges.append(GraphEdge(edge_id=f"e:rep:{cid}->{concept}", source_node_id=cid,
                                           target_node_id=f"concept:{pack}:{concept}", edge_type=EdgeType.REPRESENTS_CONCEPT,
                                           properties={"match_score": score}, provenance="lexical_match",
                                           verification_status=I, schema_version=version, confidence=round(0.5 * score + 0.3, 3)))
                    report.concepts_linked += 1
                key = col.is_primary_key or col.is_foreign_key
                if is_measure(col.name, p, key):
                    measures.append(col.name)
                if is_dimension(p) and not col.is_primary_key:
                    dims.append(col.name)
                    did = f"dim:{sid}:{cname}.{col.name}"
                    nodes.append(GraphNode(node_id=did, node_type=NodeType.DIMENSION, label=f"{cname}.{col.name}", source_id=sid,
                                           properties={"collection": cname, "column": col.name, "distinct": p.distinct_count},
                                           provenance="profile", verification_status=V, schema_version=version, confidence=1.0,
                                           evidence_refs=[_eid("profile", sid, cname, version)]))
                    embed[did] = f"{col.name} {col.description} {cname}"
                    edges.append(GraphEdge(edge_id=f"e:dimcol:{did}", source_node_id=did, target_node_id=cid,
                                           edge_type=EdgeType.DERIVED_FROM, provenance="profile", verification_status=V,
                                           schema_version=version, confidence=1.0))
                    for tv in p.top_values:
                        val = str(tv["value"])
                        vid = f"val:{sid}:{cname}.{col.name}={val}"
                        nodes.append(GraphNode(
                            node_id=vid, node_type=NodeType.VALUE_MAPPING, label=val, source_id=sid,
                            properties={"collection": cname, "column": col.name, "value": tv["value"],
                                        "normalized": normalize_text(val), "count": tv["count"]},
                            provenance="observed_value", verification_status=V, schema_version=version, confidence=1.0,
                            evidence_refs=[_eid("profile", sid, cname, version)]))
                        embed[vid] = f"{normalize_text(val)} {col.name.replace('_', ' ')}"
                        edges.append(GraphEdge(edge_id=f"e:maps:{vid}", source_node_id=cid, target_node_id=vid,
                                               edge_type=EdgeType.MAPS_TO, properties={"count": tv["count"]},
                                               provenance="observed_value", verification_status=V,
                                               schema_version=version, confidence=1.0))
                        report.value_mappings += 1
            # collection skill descriptor (stored on the collection node for retrieval)
            concepts_txt = ", ".join(c for c, _ in coll_concepts[:4])
            skill = CollectionSkillDescriptor(
                skill_id=f"skill:{sid}:{cname}", source_id=sid, collection_name=cname,
                domain_tag=schema.schema_name or adapter.descriptor.source_type.value,
                summary=(schema.description or f"Collection {cname}") + (f" Concepts: {concepts_txt}." if concepts_txt else ""),
                key_columns=[c.name for c in schema.columns if c.is_primary_key or c.is_foreign_key],
                metric_columns=measures, dimension_columns=dims, joinable_with=sorted(joinable.get(cname, set())),
                row_count=schema.row_count)
            nodes.append(GraphNode(
                node_id=coll_id(sid, cname), node_type=NodeType.COLLECTION, label=cname, source_id=sid,
                properties={"description": schema.description, "row_count": schema.row_count,
                            "primary_keys": schema.primary_keys, "schema_name": schema.schema_name,
                            "columns": [c.name for c in schema.columns], "skill": skill.model_dump()},
                provenance="catalog", verification_status=V, schema_version=version, confidence=1.0,
                evidence_refs=[_eid("catalog", sid, version)]))
            embed[coll_id(sid, cname)] = (f"{cname} {skill.summary} {' '.join(c.name for c in schema.columns)} "
                                          f"{' '.join(c.description for c in schema.columns)}")
        # metrics: pack metric shapes bound to columns representing the required measure concepts
        for pack in self.packs:
            for m in pack.get("metrics", []):
                for cid, matches in col_concepts.items():
                    concept_scores = dict(matches)
                    if not all(concept_scores.get(c, 0) >= 1.0 for c in m["measure_concepts"]):
                        continue
                    _, rest = cid.split(":", 1)
                    _sid, full = rest.split(":", 1)
                    cname, column = full.rsplit(".", 1)
                    p = profiles[(cname, column)]
                    if not is_measure(column, p, False):
                        continue
                    mid = f"metric:{sid}:{m['name']}:{cname}.{column}"
                    sql = f"SELECT {m['aggregation']}({adapter.quote(column)}) AS value FROM {adapter.quote(cname)}"
                    res = adapter.execute(sql, max_rows=1)
                    ev = EvidenceReference(evidence_id=_eid("metric", mid, version), source_id=sid, collection_name=cname,
                                           column_name=column, evidence_type="query_result", query_or_action=sql,
                                           observed_summary=f"executed: {res.rows[0] if res.succeeded else res.error_message}",
                                           sample_data=res.rows[:1])
                    evidence.append(ev)
                    nodes.append(GraphNode(
                        node_id=mid, node_type=NodeType.METRIC, label=m["label"], source_id=sid,
                        properties={"metric": m["name"], "aggregation": m["aggregation"], "collection": cname,
                                    "column": column, "synonyms": m.get("synonyms", []), "definition_sql": sql,
                                    "unit_hint": self._unit_hint(column, pack)},
                        provenance=f"pack_metric_binding:{pack['name']}",
                        # executable definitions start as candidates; promoted only after verification below
                        verification_status=I, schema_version=version, confidence=0.75, evidence_refs=[ev.evidence_id]))
                    embed[mid] = f"{m['label']} {' '.join(m.get('synonyms', []))} {column.replace('_', ' ')}"
                    edges.append(GraphEdge(edge_id=f"e:measures:{mid}", source_node_id=mid, target_node_id=cid,
                                           edge_type=EdgeType.MEASURES, provenance="pack_metric_binding",
                                           verification_status=V if res.succeeded else I, schema_version=version,
                                           confidence=0.9, evidence_refs=[ev.evidence_id]))
                    for d in schemas[cname].columns:
                        if is_dimension(profiles[(cname, d.name)]) and not d.is_primary_key:
                            edges.append(GraphEdge(edge_id=f"e:usesdim:{mid}->{d.name}", source_node_id=mid,
                                                   target_node_id=f"dim:{sid}:{cname}.{d.name}",
                                                   edge_type=EdgeType.USES_DIMENSION, provenance="same_collection",
                                                   verification_status=I, schema_version=version, confidence=0.7))
                    report.metrics += 1
                    m.setdefault("_verified_ids", []).append((mid, ev.evidence_id, res.succeeded))
        self.kg.add_evidence(evidence, tenant)
        self.kg.upsert_nodes(nodes, tenant, embed)
        self.kg.upsert_edges(edges, tenant, source_id=sid)
        for pack in self.packs:
            for m in pack.get("metrics", []):
                for mid, evid, ok in m.pop("_verified_ids", []):
                    if ok:
                        self.kg.promote(mid, [evid], tenant=tenant)

    @staticmethod
    def _unit_hint(column: str, pack: Dict[str, Any]) -> Optional[str]:
        toks = set(tokens(column))
        for t in pack.get("reporting_currency_tokens", []):
            if t in toks:
                return pack.get("reporting_currency")
        return None
