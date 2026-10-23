"""Compressed semantic context compiler.

Turns knowledge-graph metadata (schemas, profiles, relationships, reconciliation rules,
metrics, concept aliases, value dictionaries, samples, verified query patterns) into a compact
structured context for the Hercules agent.

Compression techniques: shared column dictionary + schema templates (deduplicate recurring
column signatures), dictionary-encoded value lists (identical lists stored once), compact
column codes, truncated descriptions/samples, and relevance-ranked selective retrieval when
the budget is exceeded.  This is lossy *semantic* compression: actual rows are never assumed
to be in the context -- agents still query the data through the gateway.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from udre.contracts.models import CompiledSemanticContext, CompressionStats, EdgeType, NodeType
from udre.gateway.registry import AccessContext, SourceGateway
from udre.ontology.embedding import cosine, embed
from udre.ontology.graph import KnowledgeGraph

_TYPE_CODE = {"numeric": "n", "text": "t", "temporal": "d", "boolean": "b"}
PII_COLUMN = re.compile(r"(email|phone|mobile|ssn|tax_id|passport|iban|account_number|address|serial|imei|password|token|"
                        r"secret|principal|upn|birth)", re.I)
EMAIL_VALUE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


def mask_sample(column: str, value: Any) -> Any:
    """Mask values of sensitive-looking columns and any email-like value, wherever metadata is projected."""
    if value is None:
        return value
    s = str(value)
    if not PII_COLUMN.search(column) and not EMAIL_VALUE.search(s):
        return value
    return s[:2] + "***" if len(s) > 2 else "***"

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:  # pragma: no cover - offline without cached encoding
    def count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


def _short(text: Any, n: int) -> str:
    s = str(text or "")
    return s if len(s) <= n else s[: n - 1] + "~"


class SemanticContextCompiler:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph):
        self.gateway = gateway
        self.kg = kg
        self._cache: Dict[Tuple[str, str, str], Dict[str, Any]] = {}  # (tenant, source, version) -> partial
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ per-source extraction (incremental)
    def _source_part(self, tenant: str, source_id: str) -> Dict[str, Any]:
        version = self.kg.source_version(source_id, tenant) or "unknown"
        key = (tenant, source_id, version)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        collections = [n for n in self.kg.nodes(NodeType.COLLECTION, source_id, tenant) if n["verification_status"] != "stale_schema"]
        columns = defaultdict(list)
        for n in self.kg.nodes(NodeType.COLUMN, source_id, tenant):
            if n["verification_status"] != "stale_schema":
                columns[n["properties"]["collection"]].append(n)
        refs = [e for e in self.kg.edges(EdgeType.REFERENCES, source_id=source_id, tenant=tenant)
                if e["verification_status"] != "stale_schema"]
        recon = [e for e in self.kg.edges(EdgeType.DERIVED_FROM, source_id=source_id, tenant=tenant)
                 if e["properties"].get("rule") and e["verification_status"] == "verified_fact"]
        metrics = [n for n in self.kg.nodes(NodeType.METRIC, source_id, tenant) if n["verification_status"] != "stale_schema"]
        concept_edges = [e for e in self.kg.edges(EdgeType.REPRESENTS_CONCEPT, source_id=source_id, tenant=tenant)
                         if e["properties"].get("match_score", 0) >= 1.0]
        part = dict(source_id=source_id, version=version, collections=collections, columns=columns, refs=refs,
                    recon=recon, metrics=metrics, concept_edges=concept_edges)
        with self._lock:
            self._cache[key] = part
        return part

    # ------------------------------------------------------------------ compile
    def compile(self, ctx: AccessContext, verified_patterns: Sequence[Dict[str, Any]] = (),
                sample_rows: int = 2) -> CompiledSemanticContext:
        tenant = ctx.tenant_id
        sources = [s.source_id for s in self.gateway.sources(ctx)]
        parts = [self._source_part(tenant, sid) for sid in sources]

        raw_doc: Dict[str, Any] = {"sources": []}
        sig_counter: Counter = Counter()
        per_coll_sigs: Dict[str, List[str]] = {}
        for part in parts:
            for coll in part["collections"]:
                cname = coll["label"]
                if f"{part['source_id']}:{cname}" in ctx.denied_collections:
                    continue
                sigs = [self._col_code(c) for c in sorted(part["columns"][cname], key=lambda c: c["properties"]["column"])]
                per_coll_sigs[f"{part['source_id']}:{cname}"] = sigs
                sig_counter.update(set(sigs))

        shared = sorted(s for s, n in sig_counter.items() if n >= 3)
        shared_idx = {s: i for i, s in enumerate(shared)}
        # schema templates: identical sets of shared signatures appearing in >= 2 collections
        template_counter: Counter = Counter()
        for key, sigs in per_coll_sigs.items():
            sset = tuple(sorted(s for s in sigs if s in shared_idx))
            if len(sset) >= 2:
                template_counter[sset] += 1
        templates = {f"T{i}": [f"@{shared_idx[s]}" for s in tpl] for i, (tpl, n) in
                     enumerate(sorted(template_counter.items(), key=lambda kv: (-kv[1], kv[0]))) if n >= 2}
        tpl_lookup = {tuple(v): k for k, v in templates.items()}

        value_dict: Dict[str, List[Any]] = {}
        value_ids: Dict[str, str] = {}
        compact: List[Dict[str, Any]] = []
        samples: Dict[str, List[Dict[str, Any]]] = {}
        aliases: Dict[str, List[str]] = defaultdict(list)

        for part in parts:
            sid = part["source_id"]
            adapter = self.gateway.adapter(ctx, sid)
            raw_src = {"source_id": sid, "collections": []}
            for coll in part["collections"]:
                cname = coll["label"]
                key = f"{sid}:{cname}"
                if key not in per_coll_sigs:
                    continue
                cols = sorted(part["columns"][cname], key=lambda c: c["properties"]["column"])
                props = coll["properties"]
                raw_src["collections"].append({
                    "name": cname, "description": props.get("description"), "row_count": props.get("row_count"),
                    "primary_keys": props.get("primary_keys"),
                    "columns": [{k: c["properties"].get(k) for k in ("column", "data_type", "description", "nullable",
                                                                      "is_primary_key", "foreign_key_target", "null_ratio",
                                                                      "distinct_count", "min", "max", "mean", "top_values")}
                                for c in cols]})
                encoded: List[str] = []
                sigs = per_coll_sigs[key]
                tset = tuple(f"@{shared_idx[s]}" for s in sorted(s for s in sigs if s in shared_idx))
                tpl = tpl_lookup.get(tset)
                for c, sig in zip(cols, sigs):
                    if tpl and sig in shared_idx:
                        continue
                    code = f"@{shared_idx[sig]}" if sig in shared_idx else sig
                    p = c["properties"]
                    if p.get("is_dimension") and p.get("top_values"):
                        vals = [mask_sample(p["column"], tv["value"]) for tv in p["top_values"]]
                        vkey = hashlib.sha1(json.dumps(vals, default=str).encode()).hexdigest()
                        if vkey not in value_ids:
                            value_ids[vkey] = f"v{len(value_ids)}"
                            value_dict[value_ids[vkey]] = vals
                        code += f"={value_ids[vkey]}"
                    elif p.get("is_measure") and p.get("min") is not None and not PII_COLUMN.search(p["column"]):
                        code += f"[{_fmt(p['min'])}..{_fmt(p['max'])}]"
                    encoded.append(code)
                entry = {"s": sid, "n": cname, "d": _short(props.get("description"), 110), "r": props.get("row_count"),
                         "pk": props.get("primary_keys") or [], "c": encoded}
                if tpl:
                    entry["t"] = tpl
                compact.append(entry)
                if sample_rows:
                    res = adapter.sample_rows(cname, sample_rows)
                    if res.succeeded:
                        samples[key] = [{k: _short(mask_sample(k, v), 24) for k, v in r.items()} for r in res.rows]
            raw_doc["sources"].append(raw_src)
            for e in part["concept_edges"]:
                concept = e["target_node_id"].rsplit(":", 1)[-1]
                aliases[concept].append(e["source_node_id"].split(":", 2)[-1])

        rels = []
        for part in parts:
            for e in part["refs"]:
                p = e["properties"]
                rels.append({"s": part["source_id"], "j": f"{p['child']}.{p['child_col']}>{p['parent']}.{p['parent_col']}",
                             "v": e["verification_status"] == "verified_fact", "fan": p.get("fanout"),
                             "nulls": p.get("null_keys", 0)})
            for e in part["recon"]:
                p = e["properties"]
                rels.append({"s": part["source_id"], "rule": f"{p['parent']}.{p['parent_col']}=SUM({p['child']}.{p['child_col']})",
                             "v": True})
        metrics = [{"s": m["source_id"], "m": m["properties"]["metric"], "l": m["label"],
                    "on": f"{m['properties']['collection']}.{m['properties']['column']}",
                    "agg": m["properties"]["aggregation"], "v": m["verification_status"] == "verified_fact",
                    "unit": m["properties"].get("unit_hint"), "syn": m["properties"].get("synonyms", [])}
                   for part in parts for m in part["metrics"]]
        patterns = [{"q": _short(p.get("question"), 140), "sql": p.get("sql"), "s": p.get("source_id")}
                    for p in verified_patterns][:20]

        compressed_doc = {"shared_columns": shared, "templates": templates, "values": value_dict, "collections": compact,
                          "relationships": rels, "metrics": metrics, "aliases": dict(aliases), "patterns": patterns,
                          "samples": samples}
        raw_doc.update({"relationships_verbose": [dict(e["properties"], status=e["verification_status"])
                                                  for part in parts for e in part["refs"] + part["recon"]],
                        "metrics_verbose": [m for part in parts for m in part["metrics"]],
                        "samples_verbose": samples})
        raw_text = json.dumps(raw_doc, default=str, indent=1)
        comp_text = json.dumps(compressed_doc, default=str, separators=(",", ":"))
        raw_tokens, comp_tokens = count_tokens(raw_text), count_tokens(comp_text)
        stats = CompressionStats(
            raw_bytes=len(raw_text.encode()), compressed_bytes=len(comp_text.encode()),
            compression_ratio=round(len(raw_text) / max(len(comp_text), 1), 2), raw_tokens=raw_tokens,
            compressed_tokens=comp_tokens, token_reduction_pct=round(100 * (1 - comp_tokens / max(raw_tokens, 1)), 1),
            template_count=len(templates), dictionary_entries=len(value_dict) + len(shared), collection_count=len(compact))
        return CompiledSemanticContext(
            context_id=hashlib.sha1(comp_text.encode()).hexdigest()[:12],
            schema_version="|".join(f"{p['source_id']}@{p['version']}" for p in parts), sources=sources,
            shared_schema_templates={**templates, "shared_columns": shared}, value_dictionary=value_dict,
            compact_collections=compact, candidate_relationships=rels, business_metrics=metrics,
            semantic_aliases={k: ",".join(v) for k, v in aliases.items()}, verified_query_patterns=patterns,
            representative_samples=samples, compression_stats=stats)

    @staticmethod
    def _col_code(col_node: Dict[str, Any]) -> str:
        p = col_node["properties"]
        flags = ""
        if p.get("is_primary_key"):
            flags += "K"
        if p.get("foreign_key_target"):
            flags += "F"
        if p.get("is_measure"):
            flags += "M"
        if p.get("is_dimension"):
            flags += "D"
        if (p.get("null_ratio") or 0) > 0:
            flags += "?"
        return f"{p['column']}:{_TYPE_CODE.get(p.get('family'), 't')}{flags}"

    # ------------------------------------------------------------------ selective rendering
    def render(self, compiled: CompiledSemanticContext, question: Optional[str] = None,
               budget_tokens: int = 32000) -> Tuple[str, Dict[str, Any]]:
        """Render within a token budget. If the full context does not fit, collections are added in
        relevance order, each with only its own values, relationships, metrics, aliases and samples."""
        full = self._doc(compiled, compiled.compact_collections, with_samples=True)
        text = json.dumps(full, default=str, separators=(",", ":"))
        info: Dict[str, Any] = {"full_tokens": count_tokens(text), "collections_total": len(compiled.compact_collections)}
        if info["full_tokens"] <= budget_tokens:
            info.update(rendered_tokens=info["full_tokens"], collections_included=len(compiled.compact_collections),
                        samples_included=True)
            return text, info
        qv = embed(question or "")
        ranked = sorted(compiled.compact_collections,
                        key=lambda c: -cosine(qv, embed(f"{c['n']} {c['d']} {' '.join(c['c'])}")))
        chosen: List[Dict[str, Any]] = []
        best_text = json.dumps(self._doc(compiled, [], False), default=str, separators=(",", ":"))
        for c in ranked:
            trial = json.dumps(self._doc(compiled, chosen + [c], False), default=str, separators=(",", ":"))
            if count_tokens(trial) > budget_tokens:
                break
            chosen.append(c)
            best_text = trial
        with_samples = json.dumps(self._doc(compiled, chosen, True), default=str, separators=(",", ":"))
        use_samples = count_tokens(with_samples) <= budget_tokens
        text = with_samples if use_samples else best_text
        info.update(rendered_tokens=count_tokens(text), collections_included=len(chosen), samples_included=use_samples,
                    included=[c["n"] for c in chosen])
        return text, info

    @staticmethod
    def _doc(compiled: CompiledSemanticContext, colls: List[Dict[str, Any]], with_samples: bool) -> Dict[str, Any]:
        keys = {(c["s"], c["n"]) for c in colls}
        names = {c["n"] for c in colls}
        used_vals = {code.split("=")[-1] for c in colls for code in c["c"] if "=" in code}
        templates = {k: v for k, v in compiled.shared_schema_templates.items() if k != "shared_columns"}

        def rel_ok(r: Dict[str, Any]) -> bool:
            spec = r.get("j") or r.get("rule", "")
            tables = {part.rsplit(".", 1)[0] for part in spec.replace("SUM(", "").replace(")", "").replace("=", ">").split(">")}
            return all(t in names for t in tables)

        return {
            "shared_columns": compiled.shared_schema_templates.get("shared_columns", []),
            "templates": {k: v for k, v in templates.items() if any(c.get("t") == k for c in colls)},
            "values": {k: v for k, v in compiled.value_dictionary.items() if k in used_vals},
            "collections": colls,
            "relationships": [r for r in compiled.candidate_relationships if rel_ok(r)],
            "metrics": [m for m in compiled.business_metrics if (m["s"], m["on"].rsplit(".", 1)[0]) in keys],
            "aliases": {k: ",".join(a for a in v.split(",") if a.rsplit(".", 1)[0] in names)
                        for k, v in compiled.semantic_aliases.items()},
            "patterns": compiled.verified_query_patterns,
            "samples": {k: v for k, v in compiled.representative_samples.items()
                        if tuple(k.split(":", 1)) in keys} if with_samples else {},
        }


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)
