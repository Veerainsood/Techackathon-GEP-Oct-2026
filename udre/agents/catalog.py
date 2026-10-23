"""Build planner catalog views from (a) live tool observations or (b) the compressed context."""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from udre.agents.planner import CatalogView, ColumnInfo
from udre.contracts.models import CompiledSemanticContext
from udre.ontology.builder import match_concepts

_FAMILY = {"n": "numeric", "t": "text", "d": "temporal", "b": "boolean"}
_CODE_RE = re.compile(r"^(?P<name>[^:]+):(?P<type>[ntdb])(?P<flags>[KFMD?]*)(?:=(?P<vals>v\d+))?(?:\[(?P<range>.*)\])?$")


def _pack_meta(packs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    syn = {c: spec["synonyms"] for p in packs for c, spec in p["concepts"].items()}
    ccy = {t for p in packs for t in p.get("reporting_currency_tokens", [])} or {"usd"}
    return {"concept_synonyms": syn, "ccy": ccy}


def view_from_described(source_id: str, dialect: str, quote: Callable[[str], str], described: Sequence[Dict[str, Any]],
                        relationships: Sequence[Dict[str, Any]], packs: Sequence[Dict[str, Any]]) -> CatalogView:
    meta = _pack_meta(packs)
    view = CatalogView(source_id=source_id, dialect=dialect, quote=quote, concept_synonyms=meta["concept_synonyms"],
                       reporting_currency_tokens=meta["ccy"])
    for d in described:
        coll = d["collection"]
        view.collections[coll] = {"description": d.get("description", ""), "row_count": d.get("row_count")}
        for c in d["columns"]:
            family = _family_of(c["type"])
            view.columns[(coll, c["name"])] = ColumnInfo(
                collection=coll, column=c["name"], family=family, is_measure=bool(c.get("measure")),
                is_dimension=bool(c.get("dimension")), is_key=bool(c.get("pk") or c.get("fk")), is_pk=bool(c.get("pk")),
                fk_target=c.get("fk"), description=c.get("description") or "", top_values=c.get("top_values") or [],
                null_ratio=c.get("null_ratio") or 0.0, distinct=c.get("distinct") or 0,
                concepts=[k for k, s in match_concepts(c["name"], c.get("description") or "", packs) if s >= 0.6])
    view.relationships = [r for r in relationships if r["child"] in view.collections and r["parent"] in view.collections]
    return view


def view_from_context(ctx_doc: CompiledSemanticContext, source_id: str, dialect: str, quote: Callable[[str], str],
                      packs: Sequence[Dict[str, Any]]) -> CatalogView:
    """Decode the compressed context (shared columns, templates, value dictionary) back into a view."""
    meta = _pack_meta(packs)
    view = CatalogView(source_id=source_id, dialect=dialect, quote=quote, concept_synonyms=meta["concept_synonyms"],
                       reporting_currency_tokens=meta["ccy"])
    shared = ctx_doc.shared_schema_templates.get("shared_columns", [])
    templates = {k: v for k, v in ctx_doc.shared_schema_templates.items() if k != "shared_columns"}
    aliases: Dict[str, List[str]] = {}
    for concept, cols in ctx_doc.semantic_aliases.items():
        for c in cols.split(","):
            aliases.setdefault(c, []).append(concept)
    for entry in ctx_doc.compact_collections:
        if entry["s"] != source_id:
            continue
        coll = entry["n"]
        view.collections[coll] = {"description": entry.get("d", ""), "row_count": entry.get("r")}
        codes = list(entry["c"]) + [r for r in templates.get(entry.get("t", ""), [])]
        for code in codes:
            if code.startswith("@"):
                ref = re.match(r"@(\d+)(.*)$", code)
                code = shared[int(ref.group(1))] + ref.group(2)
            m = _CODE_RE.match(code)
            if not m:
                continue
            flags = m.group("flags") or ""
            name = m.group("name")
            vals = ctx_doc.value_dictionary.get(m.group("vals") or "", [])
            view.columns[(coll, name)] = ColumnInfo(
                collection=coll, column=name, family=_FAMILY[m.group("type")], is_measure="M" in flags,
                is_dimension="D" in flags, is_key="K" in flags or "F" in flags, is_pk="K" in flags or name in entry.get("pk", []),
                fk_target=None, top_values=vals, null_ratio=0.01 if "?" in flags else 0.0, distinct=len(vals),
                concepts=aliases.get(f"{coll}.{name}", []))
    for r in ctx_doc.candidate_relationships:
        if r["s"] != source_id or "j" not in r:
            continue
        left, right = r["j"].split(">")
        child, child_col = left.rsplit(".", 1)
        parent, parent_col = right.rsplit(".", 1)
        view.relationships.append({"child": child, "child_col": child_col, "parent": parent, "parent_col": parent_col,
                                   "fanout": r.get("fan"), "null_keys": r.get("nulls", 0),
                                   "status": "verified_fact" if r.get("v") else "inferred_candidate"})
        col = view.columns.get((child, child_col))
        if col:
            col.fk_target = f"{parent}.{parent_col}"
    for m in ctx_doc.business_metrics:
        if m["s"] == source_id:
            coll, col = m["on"].rsplit(".", 1)
            view.metric_synonyms.setdefault((coll, col), []).extend(m.get("syn", []) + [m["l"]])
    return view


def _family_of(raw: str) -> str:
    from udre.gateway.base import type_family

    return type_family(raw)
