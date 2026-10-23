"""Generate versioned, tenant-scoped reference files for the database-investigation skill.

The PostgreSQL knowledge graph stays canonical; these files are projections of it plus live
source metadata.  Layout (under the skill directory):

    references/collections/<tenant>/<source>@<version>/<collection>.md
    references/value-profiles/<tenant>/<source>@<version>/<collection>.md
    references/relationships/<tenant>/<source>@<version>.md
    references/metrics/<tenant>/<source>@<version>.md
    references/verified-queries/<tenant>/<source>@<version>.md
    generated/<tenant>/<source>.current.json      <- atomic pointer to the complete version
    generated/<tenant>/catalog.json
    generated/<tenant>/global-context.json

Atomicity: all files of a version are written first, then the pointer is swapped with
``os.replace``; readers resolve files only through the pointer, so they never see a partially
generated version.  Files only contain collections the generating tenant may access; credentials,
connection strings and tenant identifiers of external systems are never written.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from udre.compiler.semantic_compiler import mask_sample
from udre.contracts.models import EdgeType, NodeType
from udre.gateway.base import jsonable, type_family
from udre.gateway.registry import AccessContext, AccessDenied, SourceGateway
from udre.ontology.graph import KnowledgeGraph

SKILL_DIR = Path(__file__).resolve().parent / "database-investigation"
GENERATOR_VERSION = 2  # bump when the projection format changes so stale files are regenerated
_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe(name: str) -> str:
    return _SAFE.sub("_", name)[:120] or "_"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _cell(v: Any, n: int = 48) -> str:
    s = str(jsonable(v)) if v is not None else "NULL"
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "~"


@dataclass
class GenerationReport:
    tenant: str
    source_id: str
    version: str
    skipped: bool = False
    files: int = 0
    collections: int = 0
    seconds: float = 0.0
    pruned_versions: List[str] = field(default_factory=list)


class CollectionContextGenerator:
    def __init__(self, gateway: SourceGateway, kg: KnowledgeGraph, memory=None, compiler=None,
                 root: Optional[Path] = None, keep_versions: int = 2):
        self.gateway = gateway
        self.kg = kg
        self.memory = memory
        self.compiler = compiler
        self.root = Path(root or os.environ.get("UDRE_SKILL_DIR", SKILL_DIR))
        self.keep_versions = keep_versions
        self._locks: Dict[Tuple[str, str], threading.Lock] = {}
        self._glock = threading.Lock()

    # ------------------------------------------------------------------ paths
    def _pointer(self, tenant: str, source_id: str) -> Path:
        return self.root / "generated" / _safe(tenant) / f"{_safe(source_id)}.current.json"

    def _vdir(self, category: str, tenant: str, source_id: str, version: str) -> Path:
        return self.root / "references" / category / _safe(tenant) / f"{_safe(source_id)}@{version}"

    def _lock(self, tenant: str, source_id: str) -> threading.Lock:
        with self._glock:
            return self._locks.setdefault((tenant, source_id), threading.Lock())

    def delete_tenant(self, tenant: str) -> None:
        shutil.rmtree(self.root / "generated" / _safe(tenant), ignore_errors=True)
        for d in (self.root / "references").glob(f"*/{_safe(tenant)}"):
            shutil.rmtree(d, ignore_errors=True)

    def current(self, tenant: str, source_id: str) -> Optional[Dict[str, Any]]:
        p = self._pointer(tenant, source_id)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------ inputs fingerprint
    def _verified_queries(self, ctx: AccessContext, source_id: str, version: str) -> List[Dict[str, Any]]:
        if not self.memory:
            return []
        return [i for i in self.memory.by_kind(ctx, "query_template", source_id) if i["schema_version"] == version]

    def _inputs_hash(self, ctx: AccessContext, source_id: str, version: str, colls: Sequence[str]) -> str:
        # Verified queries are deliberately excluded: they change after every verified answer and are
        # refreshed with generate(force=True); schema/ontology changes trigger regeneration automatically.
        n_nodes = len(self.kg.nodes(source_id=source_id, tenant=ctx.tenant_id))
        n_edges = len(self.kg.edges(source_id=source_id, tenant=ctx.tenant_id))
        cross = len([e for e in self.kg.edges(EdgeType.MAPS_TO, tenant=ctx.tenant_id)
                     if e["properties"].get("kind") == "cross_source_candidate"])
        payload = json.dumps([GENERATOR_VERSION, version, sorted(colls), cross, sorted(ctx.denied_collections), n_nodes, n_edges],
                             default=str)
        return hashlib.sha1(payload.encode()).hexdigest()[:10]

    # ------------------------------------------------------------------ generate
    def generate(self, ctx: AccessContext, source_id: str, force: bool = False, only_collections: Optional[List[str]] = None,
                 data_version: Optional[str] = None) -> GenerationReport:
        """Generate a new version. With ``only_collections`` (data-only change), the other collections' files are
        copied forward from the current version unchanged, so only changed collections are re-read."""
        t0 = time.time()
        adapter = self.gateway.adapter(ctx, source_id)  # raises AccessDenied for unauthorized sources
        tenant = ctx.tenant_id
        source_version = self.kg.source_version(source_id, tenant)
        if not source_version:
            raise RuntimeError(f"ontology for {source_id} has not been built for tenant {tenant}")
        colls = self.gateway.collections(ctx, source_id)
        inputs = self._inputs_hash(ctx, source_id, source_version, colls)
        if data_version:
            inputs = hashlib.sha1(f"{inputs}|{data_version}|{sorted(only_collections or [])}".encode()).hexdigest()[:10]
        version = f"{source_version}-{inputs}"
        report = GenerationReport(tenant=tenant, source_id=source_id, version=version)
        with self._lock(tenant, source_id):
            cur = self.current(tenant, source_id)
            if cur and cur["version"] == version and not force:
                report.skipped = True
                return report
            copy_forward = bool(only_collections) and cur is not None and cur.get("source_version") == source_version
            data = self._collect(ctx, source_id, source_version, colls)
            files: Dict[str, str] = {}
            for cname in colls:
                if cname not in data["collections"]:
                    continue
                if copy_forward and cname not in only_collections and f"collections/{cname}" in cur["files"]:
                    for kind in ("collections", "value-profiles"):
                        old = cur["files"].get(f"{kind}/{cname}")
                        if old and (self.root / old).exists():
                            p = self._vdir(kind, tenant, source_id, version) / f"{_safe(cname)}.md"
                            text = (self.root / old).read_text(encoding="utf-8")
                            text = re.sub(r'context_version: "[^"]*"', f'context_version: "{version}"\n'
                                                                       f'carried_forward_from: "{cur["version"]}"', text, count=1)
                            _atomic_write(p, text)
                            files[f"{kind}/{cname}"] = str(p.relative_to(self.root))
                    continue
                md = self._collection_md(ctx, adapter, source_id, cname, source_version, version, data)
                p = self._vdir("collections", tenant, source_id, version) / f"{_safe(cname)}.md"
                _atomic_write(p, md)
                files[f"collections/{cname}"] = str(p.relative_to(self.root))
                vp = self._value_profile_md(adapter, source_id, cname, version, data)
                p2 = self._vdir("value-profiles", tenant, source_id, version) / f"{_safe(cname)}.md"
                _atomic_write(p2, vp)
                files[f"value-profiles/{cname}"] = str(p2.relative_to(self.root))
                report.collections += 1
            for category, text in (("relationships", self._relationships_md(source_id, version, data)),
                                   ("metrics", self._metrics_md(source_id, version, data)),
                                   ("verified-queries", self._verified_md(source_id, version, data))):
                p = self.root / "references" / category / _safe(tenant) / f"{_safe(source_id)}@{version}.md"
                _atomic_write(p, text)
                files[category] = str(p.relative_to(self.root))
            pointer = {"source_id": source_id, "version": version, "source_version": source_version,
                       "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": files,
                       "catalog": data["catalog"], "provenance": adapter.provenance()}
            _atomic_write(self._pointer(tenant, source_id), json.dumps(pointer, indent=1, default=str))
            report.files = len(files)
            report.pruned_versions = self._prune(tenant, source_id, keep={version, cur["version"] if cur else ""})
        self._write_tenant_indexes(ctx)
        report.seconds = round(time.time() - t0, 2)
        return report

    def _prune(self, tenant: str, source_id: str, keep: set) -> List[str]:
        pruned = []
        base = self.root / "references"
        for cat_dir in base.glob(f"*/{_safe(tenant)}"):
            for p in cat_dir.glob(f"{_safe(source_id)}@*"):
                ver = p.name.split("@", 1)[1].removesuffix(".md")
                if ver not in keep:
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                    pruned.append(ver)
        return sorted(set(pruned))

    # ------------------------------------------------------------------ data collection from the KG
    def _collect(self, ctx: AccessContext, source_id: str, version: str, colls: Sequence[str]) -> Dict[str, Any]:
        tenant = ctx.tenant_id
        allowed = set(colls)
        collections = {n["label"]: n for n in self.kg.nodes(NodeType.COLLECTION, source_id, tenant)
                       if n["label"] in allowed and n["verification_status"] != "stale_schema"}
        columns: Dict[str, List[Dict[str, Any]]] = {}
        for n in self.kg.nodes(NodeType.COLUMN, source_id, tenant):
            if n["verification_status"] != "stale_schema" and n["properties"].get("collection") in allowed:
                columns.setdefault(n["properties"]["collection"], []).append(n)
        refs = [e for e in self.kg.edges(EdgeType.REFERENCES, source_id=source_id, tenant=tenant)
                if e["verification_status"] != "stale_schema"
                and e["properties"]["child"] in allowed and e["properties"]["parent"] in allowed]
        rules = [e for e in self.kg.edges(EdgeType.DERIVED_FROM, source_id=source_id, tenant=tenant)
                 if e["properties"].get("rule") and e["verification_status"] == "verified_fact"
                 and e["properties"]["parent"] in allowed and e["properties"]["child"] in allowed]
        concepts = [e for e in self.kg.edges(EdgeType.REPRESENTS_CONCEPT, source_id=source_id, tenant=tenant)
                    if e["verification_status"] != "stale_schema"]
        metrics = [n for n in self.kg.nodes(NodeType.METRIC, source_id, tenant)
                   if n["verification_status"] != "stale_schema" and n["properties"].get("collection") in allowed]
        cross = [e for e in self.kg.edges(EdgeType.MAPS_TO, tenant=tenant)
                 if e["properties"].get("kind") == "cross_source_candidate"
                 and source_id in (e["properties"].get("left_source"), e["properties"].get("right_source"))
                 and self._cross_allowed(ctx, e["properties"])]
        verified = self._verified_queries(ctx, source_id, version)
        catalog = []
        for cname, node in sorted(collections.items()):
            skill = node["properties"].get("skill") or {}
            catalog.append({"source_id": source_id, "collection": cname,
                            "summary": (skill.get("summary") or node["properties"].get("description") or "")[:180],
                            "rows": node["properties"].get("row_count"), "metrics": skill.get("metric_columns", [])[:8],
                            "dimensions": skill.get("dimension_columns", [])[:8], "joinable_with": skill.get("joinable_with", []),
                            "source_version": version})
        return dict(collections=collections, columns=columns, refs=refs, rules=rules, concepts=concepts, metrics=metrics,
                    cross=cross, verified=verified, catalog=catalog)

    def _cross_allowed(self, ctx: AccessContext, p: Dict[str, Any]) -> bool:
        try:
            for side in ("left", "right"):
                self.gateway.adapter(ctx, p[f"{side}_source"])
                self.gateway.check_collection(ctx, p[f"{side}_source"], p[f"{side}_collection"])
            return True
        except (AccessDenied, KeyError):
            return False

    # ------------------------------------------------------------------ markdown builders
    def _front_matter(self, **kw: Any) -> List[str]:
        lines = ["---", "kind: udre.reference", "trust: untrusted_data"]
        lines += [f"{k}: {json.dumps(v, default=str)}" for k, v in kw.items()]
        return lines + ["---", ""]

    def _collection_md(self, ctx, adapter, source_id, cname, source_version, version, data) -> str:
        node = data["collections"][cname]
        props = node["properties"]
        try:
            schema = adapter.describe_collection(cname)
        except KeyError:
            schema = None
        cols = sorted(data["columns"].get(cname, []), key=lambda n: n["properties"]["column"])
        col_concepts: Dict[str, List[Tuple[str, float]]] = {}
        coll_concepts: List[str] = []
        for e in data["concepts"]:
            concept = e["target_node_id"].rsplit(":", 1)[-1]
            if e["source_node_id"].startswith(f"col:{source_id}:{cname}."):
                col_concepts.setdefault(e["source_node_id"].rsplit(".", 1)[-1], []).append((concept, e["properties"].get("match_score", 0)))
            elif e["source_node_id"] == f"coll:{source_id}:{cname}":
                coll_concepts.append(concept)
        prov = adapter.provenance()
        out = self._front_matter(source_id=source_id, collection=cname, source_version=source_version,
                                 context_version=version, data_mode=prov.get("mode"), synced_at=prov.get("synced_at"),
                                 generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        out += [f"# {cname}", "", f"Source `{source_id}` ({adapter.descriptor.source_type.value}, {prov.get('mode')}"
                + (f", synced {prov.get('synced_at')}" if prov.get("synced_at") else "") + f"). Rows: {props.get('row_count')}.", ""]
        if props.get("description"):
            out += [f"> {props['description']}", ""]
        # ---------------- authoritative metadata
        out += ["## Authoritative metadata (catalog)", "", "| column | type | nullable | key | references |", "|---|---|---|---|---|"]
        schema_cols = {c.name: c for c in schema.columns} if schema else {}
        for n in cols:
            p = n["properties"]
            sc = schema_cols.get(p["column"])
            key = "PK" if p.get("is_primary_key") else ("FK" if p.get("foreign_key_target") else "")
            out.append(f"| `{p['column']}` | {p.get('data_type')} | {'yes' if (sc.nullable if sc else p.get('nullable')) else 'no'} "
                       f"| {key} | {p.get('foreign_key_target') or ''} |")
        out += ["", f"Primary key: {', '.join(props.get('primary_keys') or []) or 'none declared'}"]
        if schema and schema.indexes:
            out += ["", "Indexes:"] + [f"- `{i}`" for i in schema.indexes]
        out.append("")
        # ---------------- column semantics
        out += ["## Column semantics", ""]
        for n in cols:
            p = n["properties"]
            role = "measure" if p.get("is_measure") else "dimension" if p.get("is_dimension") else "key" if p.get("is_key") else "attribute"
            desc = p.get("description") or ""
            concepts = ", ".join(c for c, _ in sorted(col_concepts.get(p["column"], []), key=lambda x: -x[1]))
            out.append(f"- `{p['column']}` ({role}{', ' + type_family(p.get('data_type', '')) if p.get('data_type') else ''})"
                       + (f": {desc}" if desc else "") + (f" [concepts (inferred): {concepts}]" if concepts else ""))
        out.append("")
        # ---------------- observed facts
        out += ["## Observed facts (profiled from data)", "", "| column | nulls | distinct | min | max | mean | top values (count) |",
                "|---|---|---|---|---|---|---|"]
        for n in cols:
            p = n["properties"]
            c = p["column"]
            tops = ", ".join(f"{_cell(mask_sample(c, t['value']), 28)} ({t['count']})" for t in (p.get("top_values") or [])[:8])
            more = " ..." if len(p.get("top_values") or []) > 8 else ""
            mean = f"{p['mean']:.4g}" if isinstance(p.get("mean"), (int, float)) else ""
            out.append(f"| `{c}` | {p.get('null_ratio', 0):.1%} | {p.get('distinct_count')} | {_cell(mask_sample(c, p.get('min')), 24)} "
                       f"| {_cell(mask_sample(c, p.get('max')), 24)} | {mean} | {tops}{more} |")
        out += ["", f"Full categorical lists: `value-profiles` file for this collection.", ""]
        rels = [e for e in data["refs"] if cname in (e["properties"]["child"], e["properties"]["parent"])]
        out += ["### Relationships", ""]
        for e in rels:
            p = e["properties"]
            status = "VERIFIED" if e["verification_status"] == "verified_fact" else "CANDIDATE"
            out.append(f"- [{status}, {'declared FK' if p.get('declared') else 'inferred'}] `{p['child']}.{p['child_col']}` -> "
                       f"`{p['parent']}.{p['parent_col']}` (containment {p.get('containment')}, avg rows per parent key "
                       f"{p.get('fanout')}, null keys {p.get('null_keys')})")
        if not rels:
            out.append("- none discovered")
        rules = [e["properties"] for e in data["rules"] if cname in (e["properties"]["parent"], e["properties"]["child"])]
        if rules:
            out += ["", "### Reconciliation rules (verified by execution)", ""]
            out += [f"- `{r['parent']}.{r['parent_col']}` = SUM(`{r['child']}.{r['child_col']}`) joined on "
                    f"`{r['join_child']}` -> `{r['join_parent']}` ({r['keys_compared']} keys compared)" for r in rules]
        sample = adapter.sample_rows(cname, 3)
        if sample.succeeded and sample.rows:
            cols_s = list(sample.rows[0])[:12]
            out += ["", "### Representative rows (sensitive-looking columns masked)", "",
                    "| " + " | ".join(cols_s) + " |", "|" + "---|" * len(cols_s)]
            out += ["| " + " | ".join(_cell(mask_sample(c, r.get(c)), 24) for c in cols_s) + " |" for r in sample.rows]
        out.append("")
        # ---------------- metrics and verified queries
        mets = [m for m in data["metrics"] if m["properties"].get("collection") == cname]
        if mets:
            out += ["## Metrics", ""]
            out += [f"- {m['label']}: {m['properties']['aggregation']}(`{m['properties']['column']}`) "
                    f"[{'definition executes (verified)' if m['verification_status'] == 'verified_fact' else 'candidate'}; "
                    f"terminology binding inferred]" for m in mets]
            out.append("")
        vq = [i for i in data["verified"] if cname in (i["payload"].get("sql") or "") or cname.split(".")[-1] in (i["payload"].get("sql") or "")]
        if vq:
            out += ["## Verified query examples", ""]
            for i in vq[:5]:
                out += [f"- Q: {i['question']}", f"  ```sql\n  {i['payload']['sql']}\n  ```"]
            out.append("")
        # ---------------- inferred hypotheses
        out += ["## Inferred hypotheses (not verified; confirm with data before relying on them)", ""]
        if coll_concepts:
            out.append(f"- Collection may represent: {', '.join(coll_concepts)} (lexical match to terminology pack)")
        cand = [e for e in rels if e["verification_status"] != "verified_fact"]
        out += [f"- Candidate join `{e['properties']['child']}.{e['properties']['child_col']}` -> "
                f"`{e['properties']['parent']}.{e['properties']['parent_col']}`" for e in cand]
        for e in data["cross"]:
            p = e["properties"]
            if cname in (p.get("left_collection"), p.get("right_collection")):
                out.append(f"- Cross-source candidate: `{p['left_source']}:{p['left_collection']}.{p['left_column']}` ~ "
                           f"`{p['right_source']}:{p['right_collection']}.{p['right_column']}` ({p['matches']} normalized "
                           f"value matches; {p['basis']}). Not an identity assertion.")
        out.append("")
        return "\n".join(out)

    def _value_profile_md(self, adapter, source_id, cname, version, data) -> str:
        out = self._front_matter(source_id=source_id, collection=cname, context_version=version)
        out += [f"# Value profile: {cname}", ""]
        for n in sorted(data["columns"].get(cname, []), key=lambda n: n["properties"]["column"]):
            p = n["properties"]
            if not p.get("is_dimension"):
                continue
            q = adapter.quote(p["column"]) if hasattr(adapter, "quote") else p["column"]
            t = adapter.quote(cname) if hasattr(adapter, "quote") else cname
            res = adapter.execute(f"SELECT CAST({q} AS VARCHAR) AS v, COUNT(*) AS n FROM {t} GROUP BY 1 ORDER BY 2 DESC LIMIT 200",
                                  max_rows=200)
            vals = res.rows if res.succeeded else [{"v": t_["value"], "n": t_["count"]} for t_ in p.get("top_values", [])]
            out += [f"## `{p['column']}` ({p.get('distinct_count')} distinct)", ""]
            out += [f"- `{_cell(mask_sample(p['column'], r['v']), 80)}`: {r['n']}" for r in vals]
            out.append("")
        return "\n".join(out)

    def _relationships_md(self, source_id, version, data) -> str:
        out = self._front_matter(source_id=source_id, context_version=version) + [f"# Relationships: {source_id}", ""]
        out += ["| from | to | status | origin | avg rows per parent | null keys |", "|---|---|---|---|---|---|"]
        for e in sorted(data["refs"], key=lambda e: e["edge_id"]):
            p = e["properties"]
            out.append(f"| `{p['child']}.{p['child_col']}` | `{p['parent']}.{p['parent_col']}` | {e['verification_status']} | "
                       f"{e['provenance']} | {p.get('fanout')} | {p.get('null_keys')} |")
        if data["rules"]:
            out += ["", "## Reconciliation rules", ""]
            out += [f"- `{e['properties']['parent']}.{e['properties']['parent_col']}` = SUM(`{e['properties']['child']}."
                    f"{e['properties']['child_col']}`)" for e in data["rules"]]
        if data["cross"]:
            out += ["", "## Cross-source identity candidates (unverified)", ""]
            out += [f"- `{e['properties']['left_source']}:{e['properties']['left_collection']}.{e['properties']['left_column']}` ~ "
                    f"`{e['properties']['right_source']}:{e['properties']['right_collection']}.{e['properties']['right_column']}` "
                    f"({e['properties']['matches']} matches, {e['properties']['basis']})" for e in data["cross"]]
        return "\n".join(out) + "\n"

    def _metrics_md(self, source_id, version, data) -> str:
        out = self._front_matter(source_id=source_id, context_version=version) + [f"# Metrics: {source_id}", ""]
        for m in sorted(data["metrics"], key=lambda m: m["node_id"]):
            p = m["properties"]
            out += [f"## {m['label']} -> `{p['collection']}.{p['column']}`", "",
                    f"- aggregation: {p['aggregation']}; unit: {p.get('unit_hint') or 'unspecified'}",
                    f"- status: {m['verification_status']} (definition executed; the business-term binding is inferred)",
                    f"- synonyms: {', '.join(p.get('synonyms', []))}", f"- definition: `{p.get('definition_sql')}`", ""]
        return "\n".join(out) + "\n"

    def _verified_md(self, source_id, version, data) -> str:
        out = self._front_matter(source_id=source_id, context_version=version) + [f"# Verified queries: {source_id}", ""]
        if not data["verified"]:
            out.append("No verified queries yet for this source version.")
        for i in data["verified"]:
            out += [f"## {i['question']}", "", "```sql", i["payload"]["sql"], "```", "", f"Answer at verification: {i['payload'].get('answer')}", ""]
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------ tenant indexes
    def _write_tenant_indexes(self, ctx: AccessContext) -> None:
        tenant = ctx.tenant_id
        catalog = []
        for s in self.gateway.sources(ctx):
            cur = self.current(tenant, s.source_id)
            if cur:
                allowed = set(self.gateway.collections(ctx, s.source_id))
                catalog += [dict(c, provenance_mode=cur.get("provenance", {}).get("mode")) for c in cur["catalog"]
                            if c["collection"] in allowed]
        base = self.root / "generated" / _safe(tenant)
        _atomic_write(base / "catalog.json", json.dumps({"tenant_scope": "current", "collections": catalog}, indent=1, default=str))
        if self.compiler is not None:
            compiled = self.compiler.compile(ctx)
            _text, info = self.compiler.render(compiled, None, budget_tokens=10 ** 9)
            doc = {"context_id": compiled.context_id, "schema_version": compiled.schema_version,
                   "compression": compiled.compression_stats.model_dump(), "inclusion": info,
                   "context": json.loads(_text)}
            _atomic_write(base / "global-context.json", json.dumps(doc, default=str))

    # ------------------------------------------------------------------ read API (permission-checked)
    def catalog(self, ctx: AccessContext) -> List[Dict[str, Any]]:
        out = []
        for s in self.gateway.sources(ctx):
            cur = self.current(ctx.tenant_id, s.source_id)
            if not cur:
                continue
            allowed = set(self.gateway.collections(ctx, s.source_id))
            out += [dict(c, provenance_mode=cur.get("provenance", {}).get("mode")) for c in cur["catalog"] if c["collection"] in allowed]
        return out

    def list_collection_contexts(self, ctx: AccessContext, source_id: str) -> List[Dict[str, Any]]:
        self.gateway.adapter(ctx, source_id)
        cur = self.current(ctx.tenant_id, source_id)
        if not cur:
            return []
        allowed = set(self.gateway.collections(ctx, source_id))
        return [{"source_id": source_id, "collection": c["collection"], "summary": c["summary"], "rows": c["rows"],
                 "context_version": cur["version"]} for c in cur["catalog"] if c["collection"] in allowed]

    def load_collection_context(self, ctx: AccessContext, source_id: str, collection: str,
                                include_value_profile: bool = False) -> Dict[str, Any]:
        self.gateway.adapter(ctx, source_id)
        self.gateway.check_collection(ctx, source_id, collection)
        if collection not in set(self.gateway.collections(ctx, source_id)):
            raise AccessDenied(f"collection {collection} is not available")
        cur = self.current(ctx.tenant_id, source_id)
        if not cur or f"collections/{collection}" not in cur["files"]:
            raise KeyError(f"no generated context for {source_id}:{collection}; run generation first")
        text = (self.root / cur["files"][f"collections/{collection}"]).read_text(encoding="utf-8")
        out = {"source_id": source_id, "collection": collection, "context_version": cur["version"],
               "source_version": cur["source_version"], "trust": "untrusted_data", "content": text}
        if include_value_profile and f"value-profiles/{collection}" in cur["files"]:
            out["value_profile"] = (self.root / cur["files"][f"value-profiles/{collection}"]).read_text(encoding="utf-8")
        return out

    def load_reference(self, ctx: AccessContext, source_id: str, category: str) -> Dict[str, Any]:
        self.gateway.adapter(ctx, source_id)
        cur = self.current(ctx.tenant_id, source_id)
        if not cur or category not in cur["files"]:
            raise KeyError(f"no {category} reference for {source_id}")
        return {"source_id": source_id, "category": category, "context_version": cur["version"], "trust": "untrusted_data",
                "content": (self.root / cur["files"][category]).read_text(encoding="utf-8")}
