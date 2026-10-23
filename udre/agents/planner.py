"""Schema-agnostic question -> query-plan -> SQL machinery shared by both agents.

The planner never sees table names in code. It works over a ``CatalogView`` (whatever columns,
profiles, values and relationships an agent has gathered) and produces a typed ``QueryPlan``
whose every filter is grounded in an observed value or an explicit time/null intent.  Agents
differ in how they build the catalog view, which values they trust, and their scoring weights.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field

from udre.gateway.base import normalize_text
from udre.ontology.embedding import tokens

STOP = {"what", "is", "the", "of", "in", "for", "a", "an", "and", "are", "with", "how", "many", "much", "which", "do",
        "does", "did", "have", "has", "our", "we", "all", "across", "to", "by", "per", "each", "on", "at", "from", "as",
        "that", "this", "be", "was", "were", "there", "their", "its", "it", "me", "show", "give", "list", "tell",
        "number", "count", "total", "sum", "average", "avg", "mean", "top", "highest", "lowest", "maximum", "minimum",
        "max", "min", "excluding", "except", "without", "no", "not", "status", "value", "values", "year",
        "period", "month", "within", "during", "between", "using", "whose", "where", "who", "can", "you", "please",
        "include", "including", "only", "largest", "biggest", "smallest", "mapping", "mapped", "missing", "any"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
RELATIVE_TIME = re.compile(r"\b(last|previous|prior|this|current|next)\s+(year|quarter|month|fiscal year)\b|\bytd\b|\byear to date\b", re.I)
PERIOD_RE = re.compile(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])\b")
YEAR_RE = re.compile(r"(?<![\w-])((?:19|20)\d{2})(?![\w-])")
MONTH_YEAR_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+((?:19|20)\d{2})\b", re.I)
TOPN_RE = re.compile(r"\btop\s+(\d+)\b|\b(?:largest|biggest|highest)\s+(\d+)\b", re.I)
GROUP_RE = re.compile(r"\b(?:by|per|for each|broken down by)\s+([a-z][a-z _-]*?)(?=\s+(?:for|in|with|excluding|where|during|from|and|over|across)\b|[,?.]|$)", re.I)
EXCLUDE_RE = re.compile(r"\b(?:excluding|except|other than|not including)\s+(?:the\s+)?([A-Za-z0-9_ -]+?)(?=\s+(?:invoices?|rows?|records?|items?|entries|ones)\b|[,?.]|$)", re.I)
NULL_RE = re.compile(r"\b(?:no|without|missing|lacking|unmapped)\s+(?:a\s+|an\s+|any\s+)?([a-z_ ]+?)\s*(?:mapping|link|assignment|match|reference)\b|\bunmapped\s+([a-z_]+)", re.I)
ISO_CCY = {"USD", "EUR", "GBP", "JPY", "SGD", "CAD", "AUD", "CHF", "INR", "CNY", "HKD", "SEK", "NOK", "DKK"}


# ---------------------------------------------------------------------------- catalog view
@dataclass
class ColumnInfo:
    collection: str
    column: str
    family: str
    is_measure: bool = False
    is_dimension: bool = False
    is_key: bool = False
    is_pk: bool = False
    fk_target: Optional[str] = None
    description: str = ""
    top_values: List[Any] = field(default_factory=list)
    null_ratio: float = 0.0
    distinct: int = 0
    concepts: List[str] = field(default_factory=list)


@dataclass
class CatalogView:
    source_id: str
    dialect: str
    quote: Callable[[str], str]
    columns: Dict[Tuple[str, str], ColumnInfo] = field(default_factory=dict)
    collections: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    relationships: List[Dict[str, Any]] = field(default_factory=list)
    metric_synonyms: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    concept_synonyms: Dict[str, List[str]] = field(default_factory=dict)
    reporting_currency_tokens: Set[str] = field(default_factory=lambda: {"usd"})
    learned_values: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def cols_of(self, collection: str) -> List[ColumnInfo]:
        return [c for (coll, _), c in self.columns.items() if coll == collection]

    def idf(self) -> Dict[str, float]:
        if not hasattr(self, "_idf"):
            df: Dict[str, int] = {}
            docs = [set(tokens(c.column)) | set(tokens(c.collection.split(".")[-1])) for c in self.columns.values()]
            for d in docs:
                for t in d:
                    df[t] = df.get(t, 0) + 1
            n = max(len(docs), 1)
            self._idf = {t: math.log(1 + n / v) for t, v in df.items()}
        return self._idf


# ---------------------------------------------------------------------------- plan contracts
class FilterSpec(BaseModel):
    collection: str
    column: str
    op: str  # =, <>, LIKE, YEAR, IS NULL
    value: Any = None
    origin: str  # value_match | exclusion | time | null_intent | learned_mapping
    matched_text: str = ""
    score: float = 1.0


class JoinSpec(BaseModel):
    left: str
    left_col: str
    right: str
    right_col: str
    direction: str = "n:1"


class QueryPlan(BaseModel):
    source_id: str
    primary_collection: str
    aggregation: str  # SUM COUNT AVG MAX MIN TOP LIST
    measure: Optional[Tuple[str, str]] = None
    group_by: List[Tuple[str, str]] = Field(default_factory=list)
    filters: List[FilterSpec] = Field(default_factory=list)
    joins: List[JoinSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    order_desc: bool = True
    assumptions: List[str] = Field(default_factory=list)
    ambiguities: List[Dict[str, Any]] = Field(default_factory=list)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    rationale: List[str] = Field(default_factory=list)


@dataclass
class QuestionFrame:
    text: str
    aggregation: str
    top_n: Optional[int]
    group_phrase: Optional[str]
    periods: List[str]
    years: List[int]
    relative_time: Optional[str]
    exclusions: List[str]
    null_terms: List[str]
    content: List[str]
    entity_phrase: Optional[str] = None


def parse_question(q: str) -> QuestionFrame:
    text = q.strip()
    low = text.lower()
    top = TOPN_RE.search(low)
    if re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", low):
        agg = "COUNT"
    elif re.search(r"\baverage\b|\bavg\b|\bmean\b", low):
        agg = "AVG"
    elif top:
        agg = "TOP"
    elif re.search(r"\bhighest\b|\bmaximum\b|\blargest\b|\bmax\b", low):
        agg = "MAX"
    elif re.search(r"\blowest\b|\bminimum\b|\bsmallest\b|\bmin\b", low):
        agg = "MIN"
    else:
        agg = "SUM"
    periods = [f"{y}-{m}" for y, m in PERIOD_RE.findall(text)]
    periods += [f"{y}-{MONTHS[mname.lower()]:02d}" for mname, y in MONTH_YEAR_RE.findall(text)]
    stripped = PERIOD_RE.sub(" ", text)
    years = [] if periods else [int(y) for y in YEAR_RE.findall(MONTH_YEAR_RE.sub(" ", stripped))]
    rel = RELATIVE_TIME.search(low)
    nulls = [(a or b).strip() for a, b in NULL_RE.findall(low)]
    excl = [] if nulls else [e.strip() for e in EXCLUDE_RE.findall(text)]
    group = None
    if agg != "TOP":
        g = GROUP_RE.search(low)
        group = g.group(1).strip() if g else None
    entity = None
    if agg == "TOP":
        m = re.search(r"\btop\s+\d+\s+([a-z][a-z _-]*?)(?=\s+(?:by|with|for|in|based)\b|[?.]|$)", low)
        entity = m.group(1).strip() if m else None
    code_literals = re.findall(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b", text)
    no_codes = re.sub(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b", " ", text).lower()
    content = [t for t in tokens(no_codes) if t not in STOP and not t.isdigit()]
    if not content and code_literals:
        content = [t for t in tokens(low) if t not in STOP and not t.isdigit()]
    return QuestionFrame(text=text, aggregation=agg, top_n=int(next(x for x in top.groups() if x)) if top else None,
                         group_phrase=group, periods=periods, years=years, relative_time=rel.group(0) if rel else None,
                         exclusions=excl, null_terms=nulls, content=content, entity_phrase=entity)


# ---------------------------------------------------------------------------- planner
@dataclass
class PlannerWeights:
    name: float = 2.0
    description: float = 0.6
    collection: float = 1.2
    concept: float = 1.0
    metric_synonym: float = 1.5
    unit: float = 0.4
    learned: float = 1.5
    precision: float = 1.0
    anchor: float = 4.0


class SemanticPlanner:
    def __init__(self, view: CatalogView, weights: Optional[PlannerWeights] = None,
                 join_path: Optional[Callable[[str, str], Optional[List[Dict[str, Any]]]]] = None,
                 today: Optional[date] = None):
        self.v = view
        self.w = weights or PlannerWeights()
        self.join_path = join_path or self._join_path
        self.today = today or date.today()

    # .................................................................. scoring helpers
    def _overlap(self, q: Set[str], toks: Sequence[str]) -> float:
        idf = self.v.idf()
        return sum(idf.get(t, 1.0) for t in set(toks) if t in q)

    def _measure_scores(self, frame: QuestionFrame) -> List[Tuple[float, ColumnInfo, Dict[str, float]]]:
        q = set(frame.content)
        qtext = " " + " ".join(tokens(frame.text)) + " "
        mentions_ccy = {t.lower() for t in re.findall(r"\b[A-Z]{3}\b", frame.text) if t in ISO_CCY}
        out = []
        for c in self.v.columns.values():
            if not c.is_measure:
                continue
            parts = {
                "name": self.w.name * self._overlap(q, tokens(c.column)),
                "description": self.w.description * min(self._overlap(q, tokens(c.description)), 3.0),
                "collection": self.w.collection * self._overlap(q, tokens(c.collection.split(".")[-1])),
            }
            concept = 0.0
            for con in c.concepts:
                if any(f" {' '.join(tokens(s))} " in qtext for s in self.v.concept_synonyms.get(con, [])):
                    concept += 1
            parts["concept"] = self.w.concept * concept
            syns = self.v.metric_synonyms.get((c.collection, c.column), [])
            parts["metric"] = self.w.metric_synonym * (1.0 if any(f" {' '.join(tokens(s))} " in qtext for s in syns) else 0.0)
            ctoks = set(tokens(c.column))
            if ctoks & self.v.reporting_currency_tokens and not (mentions_ccy - self.v.reporting_currency_tokens - {"usd"}):
                parts["unit"] = self.w.unit
            if parts["name"] > 0:
                parts["precision"] = self.w.precision * len(ctoks & q) / max(len(ctoks), 1)
            anchors = self._anchors(frame)
            if c.collection in anchors:
                parts["anchor"] = self.w.anchor * anchors[c.collection]
            score = sum(parts.values())
            if score > 0:
                out.append((score, c, parts))
        out.sort(key=lambda x: (-x[0], x[1].collection, x[1].column))
        return out

    def _anchors(self, frame: QuestionFrame) -> Dict[str, float]:
        """Collections holding exact literal values written in the question (e.g. CODE_VALUE, 'Fully Signed')."""
        if getattr(self, "_anchor_cache", (None,))[0] == frame.text:
            return self._anchor_cache[1]
        literals = {normalize_text(raw) for raw, _ in self._ngrams(frame.text)
                    if re.search(r"[A-Z]{2,}|_", raw) or len(raw.split()) > 1}
        out: Dict[str, float] = {}
        for c in self.v.columns.values():
            hits = sum(1 for v in c.top_values if normalize_text(v) in literals and not str(v).isdigit())
            if hits:
                out[c.collection] = out.get(c.collection, 0) + min(hits, 2)
        self._anchor_cache = (frame.text, out)
        return out

    def _entity_collection(self, phrase_tokens: Set[str]) -> List[Tuple[float, str]]:
        scored = []
        for coll, meta in self.v.collections.items():
            s = 2.0 * self._overlap(phrase_tokens, tokens(coll.split(".")[-1]))
            s += 0.5 * min(self._overlap(phrase_tokens, tokens(meta.get("description", ""))), 3.0)
            if s > 0:
                scored.append((s, coll))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored

    def _join_path(self, start: str, goal: str) -> Optional[List[Dict[str, Any]]]:
        """Best N:1 path: fewest hops, then FK names mentioned in the question, then fewest null keys."""
        if start == goal:
            return []
        adj: Dict[str, List[Dict[str, Any]]] = {}
        for r in self.v.relationships:
            adj.setdefault(r["child"], []).append(r)
        paths: List[List[Dict[str, Any]]] = []

        def walk(node: str, path: List[Dict[str, Any]], seen: Set[str]) -> None:
            if len(path) >= 3:
                return
            for r in adj.get(node, []):
                if r["parent"] in seen:
                    continue
                step = dict(r, direction="n:1", left=node, right=r["parent"])
                if r["parent"] == goal:
                    paths.append(path + [step])
                else:
                    walk(r["parent"], path + [step], seen | {r["parent"]})

        walk(start, [], {start})
        if not paths:
            return None
        q = getattr(self, "_qtokens", set())

        def rank(p: List[Dict[str, Any]]) -> Tuple[Any, ...]:
            mention = sum(len(set(tokens(s["child_col"])) & q) for s in p)
            nulls = sum(int(s.get("null_keys") or 0) for s in p)
            return (len(p), -mention, nulls, [(s["left"], s["child_col"]) for s in p])

        return min(paths, key=rank)

    # .................................................................. value grounding
    def _ngrams(self, text: str) -> List[Tuple[str, str]]:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_\-&]*", text)
        grams = []
        for n in (4, 3, 2, 1):
            for i in range(len(words) - n + 1):
                g = " ".join(words[i:i + n])
                ng = normalize_text(g)
                if n == 1 and (ng in STOP or len(ng) < 2):
                    continue
                grams.append((g, ng))
        return grams

    def ground_values(self, frame: QuestionFrame, scope: Sequence[str], consumed: Set[str],
                      external: Optional[List[Dict[str, Any]]] = None) -> List[FilterSpec]:
        """Map question phrases to real categorical values observed in scope collections."""
        grams = self._ngrams(frame.text)
        qtoks = set(tokens(frame.text))
        column_words = {t for c in self.v.cols_of(scope[0]) for t in tokens(c.column)} if scope else set()
        cands: List[Tuple[float, FilterSpec, str]] = []
        excl_norm = {normalize_text(e) for e in frame.exclusions}
        values: List[Tuple[ColumnInfo, Any]] = []
        for coll in scope:
            for c in self.v.cols_of(coll):
                if c.is_dimension or c.top_values:
                    values.extend((c, v) for v in c.top_values)
        for m in external or []:
            col = self.v.columns.get((m["collection_name"], m["column_name"]))
            if col and m["collection_name"] in scope:
                values.append((col, m["matched_value"]))
        for col, val in values:
            nv = normalize_text(val)
            if not nv or nv.isdigit():
                continue
            vt = tokens(str(val))
            for raw, ng in grams:
                if ng != nv:
                    continue
                exact_case = raw.replace(" ", "_") == str(val) or raw == str(val)
                if set(vt) <= consumed and len(vt) <= 1:
                    continue  # phrase already explains the chosen measure/entity (e.g. "open" in open balance)
                if len(vt) == 1 and not exact_case and not str(val).isupper() and ng in {"open", "paid", "closed", "active"}:
                    continue
                if len(vt) == 1 and not exact_case and nv in consumed:
                    continue
                if len(vt) == 1 and not exact_case and vt[0] in column_words:
                    continue  # the word names a column ("pipeline stage"), it is not a value
                score = 1.0 + 0.15 * len(vt) + (0.2 if exact_case else 0.0)
                score += 0.3 * self._overlap(qtoks, tokens(col.column)) / max(len(tokens(col.column)), 1)
                op = "<>" if any(nv == e or nv in e.split() or e in nv for e in excl_norm) else "="
                cands.append((score, FilterSpec(collection=col.collection, column=col.column, op=op, value=val,
                                                origin="exclusion" if op == "<>" else "value_match",
                                                matched_text=raw, score=round(score, 3)), ng))
                break
        for term, maps in self.v.learned_values.items():
            if f" {normalize_text(term)} " in f" {normalize_text(frame.text)} ":
                for m in maps:
                    if m.get("collection") in scope:
                        cands.append((1.0 + self.w.learned, FilterSpec(collection=m["collection"], column=m["column"], op="=",
                                                                        value=m["value"], origin="learned_mapping",
                                                                        matched_text=term), normalize_text(term)))
        cands.sort(key=lambda x: (-x[0], scope.index(x[1].collection) if x[1].collection in scope else 99))
        chosen: List[FilterSpec] = []
        used_cols: Set[Tuple[str, str]] = set()
        used_spans: List[str] = []
        for score, f, span in cands:
            if (f.collection, f.column) in used_cols:
                continue
            if any(span == s or f" {span} " in f" {s} " for s in used_spans):
                continue
            chosen.append(f)
            used_cols.add((f.collection, f.column))
            used_spans.append(span)
        return chosen

    def _time_columns(self, coll: str) -> List[ColumnInfo]:
        out = []
        for c in self.v.cols_of(coll):
            if c.family == "temporal":
                out.append(c)
            elif c.family == "text" and c.top_values and all(re.fullmatch(r"\d{4}-\d{2}", str(v)) for v in c.top_values[:5]):
                out.append(c)
            elif c.family == "numeric" and "year" in tokens(c.column):
                out.append(c)
        return out

    def _year_filter(self, frame: QuestionFrame, year: int, colls: Sequence[str]) -> Optional[FilterSpec]:
        q = set(tokens(frame.text))
        best: Optional[Tuple[float, ColumnInfo]] = None
        for rank, coll in enumerate(colls):
            for c in self._time_columns(coll):
                s = self._overlap(q, tokens(c.column)) - 0.5 * rank
                s += 0.3 if "period" in tokens(c.column) else 0.0
                s -= 0.4 if {"due", "created", "start", "end", "expiration", "completed", "started"} & set(tokens(c.column)) else 0.0
                if best is None or s > best[0]:
                    best = (s, c)
        if not best:
            return None
        c = best[1]
        if c.family == "temporal":
            return FilterSpec(collection=c.collection, column=c.column, op="YEAR", value=year, origin="time", matched_text=str(year))
        if c.family == "numeric":
            return FilterSpec(collection=c.collection, column=c.column, op="=", value=year, origin="time", matched_text=str(year))
        return FilterSpec(collection=c.collection, column=c.column, op="LIKE", value=f"{year}-%", origin="time", matched_text=str(year))

    def _period_filter(self, period: str, colls: Sequence[str], existing: List[FilterSpec]) -> Optional[FilterSpec]:
        if any(str(f.value) == period for f in existing):
            return None
        for coll in colls:
            for c in self._time_columns(coll):
                if c.family == "text":
                    return FilterSpec(collection=coll, column=c.column, op="=", value=period, origin="time", matched_text=period)
        return None

    # .................................................................. main entry
    def plan(self, question: str) -> QueryPlan:
        frame = parse_question(question)
        self._qtokens = set(frame.content)
        self._group_via: Optional[JoinSpec] = None
        rationale: List[str] = [f"aggregation={frame.aggregation}"]
        measures = self._measure_scores(frame)
        consumed: Set[str] = set()
        measure: Optional[ColumnInfo] = None
        ambiguities: List[Dict[str, Any]] = []
        if frame.aggregation in ("SUM", "AVG", "MAX", "MIN", "TOP") and measures:
            measure = measures[0][1]
            consumed |= set(tokens(measure.column))
            rationale.append(f"measure={measure.collection}.{measure.column} score={measures[0][0]:.2f} {measures[0][2]}")
            rivals = [m for m in measures[1:6] if m[1].collection != measure.collection and m[0] >= 0.85 * measures[0][0]]
            informative = [t for t in frame.content if t in set(tokens(measure.column))]
            if rivals and len(informative) <= 1:
                ambiguities.append({"type": "metric_choice", "detail": "several measures fit the question comparably",
                                    "options": [f"{m[1].collection}.{m[1].column}" for m in [measures[0]] + rivals[:3]]})
        # primary collection
        if measure is not None:
            primary = measure.collection
        else:
            ent_tokens = set(frame.content)
            ranked = self._entity_collection(ent_tokens)
            if not ranked:
                raise ValueError("no collection matches the question")
            primary = ranked[0][1]
            consumed |= set(tokens(primary.split(".")[-1]))
            rationale.append(f"entity collection={primary} score={ranked[0][0]:.2f}")
        reachable = [primary] + [c for c in self.v.collections if c != primary and self.join_path(primary, c) is not None]
        # group by / top-n entity
        group_by: List[Tuple[str, str]] = []
        if frame.aggregation == "TOP" and frame.entity_phrase:
            ent = set(tokens(frame.entity_phrase))
            cands = [(s, c) for s, c in self._entity_collection(ent) if c in reachable]
            if cands:
                target = cands[0][1]
                label = self._label_column(target)
                if label:
                    group_by.append((target, label))
                    consumed |= ent
        elif frame.group_phrase:
            gcol = self._group_column(frame.group_phrase, reachable)
            if gcol:
                group_by.append(gcol)
                consumed |= set(tokens(frame.group_phrase))
        # value grounding (filters)
        filters = self.ground_values(frame, reachable, consumed, external=getattr(self, "external_matches", None))
        for nt in frame.null_terms:
            ntoks = set(tokens(nt))
            for c in self.v.cols_of(primary):
                if ntoks & set(tokens(c.column)) and c.null_ratio > 0:
                    filters.append(FilterSpec(collection=primary, column=c.column, op="IS NULL", origin="null_intent", matched_text=nt))
                    break
        filter_colls = [f.collection for f in filters]
        time_scope = [primary] + [c for c in dict.fromkeys(filter_colls) if c != primary]
        for p in frame.periods:
            f = self._period_filter(p, time_scope, filters)
            if f:
                filters.append(f)
        assumptions: List[str] = []
        years = list(frame.years)
        if frame.relative_time:
            ref = self.today.year - 1 if re.search(r"last|previous|prior", frame.relative_time, re.I) else self.today.year
            years.append(ref)
            ambiguities.append({"type": "relative_time", "detail": f"'{frame.relative_time}' interpreted as calendar year {ref}"})
            assumptions.append(f"'{frame.relative_time}' interpreted as calendar year {ref} (fiscal calendar not confirmed)")
        for y in years:
            if not any(f.origin == "time" for f in filters):
                f = self._year_filter(frame, y, time_scope)
                if f:
                    filters.append(f)
        # joins for every non-primary collection referenced
        joins: List[JoinSpec] = []
        needed = list(dict.fromkeys([f.collection for f in filters] + [g[0] for g in group_by]))
        via = self._group_via
        if via is not None and group_by and group_by[0][0] == via.right:
            if via.left != primary:
                for step in self.join_path(primary, via.left) or []:
                    j = JoinSpec(left=step["left"], left_col=step["child_col"], right=step["right"], right_col=step["parent_col"])
                    if j not in joins:
                        joins.append(j)
            joins.append(via)
            needed = [c for c in needed if c != via.right]
        for coll in needed:
            if coll == primary or any(j.right == coll for j in joins):
                continue
            path = self.join_path(primary, coll) or []
            for step in path:
                j = JoinSpec(left=step["left"], left_col=step["child_col"], right=step["right"], right_col=step["parent_col"])
                if j not in joins:
                    joins.append(j)
        limit = frame.top_n if frame.aggregation == "TOP" else None
        if frame.aggregation in ("MAX", "MIN") and group_by:
            limit = 1
        cand = [{"column": f"{m[1].collection}.{m[1].column}", "score": round(m[0], 3)} for m in measures[:5]]
        return QueryPlan(source_id=self.v.source_id, primary_collection=primary, aggregation=frame.aggregation,
                         measure=(measure.collection, measure.column) if measure else None, group_by=group_by,
                         filters=filters, joins=joins, limit=limit, assumptions=assumptions, ambiguities=ambiguities,
                         candidates=cand, rationale=rationale)

    def _label_column(self, coll: str) -> Optional[str]:
        cols = self.v.cols_of(coll)
        for pref in ("name", "label", "title"):
            for c in cols:
                if c.family == "text" and pref in tokens(c.column) and not c.is_pk:
                    return c.column
        pk = [c for c in cols if c.is_pk]
        return pk[0].column if pk else None

    def _group_column(self, phrase: str, colls: Sequence[str]) -> Optional[Tuple[str, str]]:
        ptoks = set(tokens(phrase))
        best: Optional[Tuple[float, ColumnInfo]] = None
        for rank, coll in enumerate(colls):
            for c in self.v.cols_of(coll):
                if c.family not in ("text", "boolean") and not c.fk_target:
                    continue
                s = self._overlap(ptoks, tokens(c.column)) - 0.3 * rank + (0.3 if c.is_dimension else 0)
                if s > 0.5 and (best is None or s > best[0]):
                    best = (s, c)
        if not best:
            return None
        c = best[1]
        if c.fk_target:  # group by a readable label on the referenced collection, joined through *this* key
            parent, parent_col = c.fk_target.rsplit(".", 1)
            if parent in self.v.collections:
                label = self._label_column(parent)
                if label and label != parent_col:
                    self._group_via = JoinSpec(left=c.collection, left_col=c.column, right=parent, right_col=parent_col)
                    return (parent, label)
        return (c.collection, c.column)


# ---------------------------------------------------------------------------- SQL compilation
def compile_sql(plan: QueryPlan, view: CatalogView) -> str:
    q = view.quote
    aliases: Dict[str, str] = {plan.primary_collection: "t0"}
    joins_sql = []
    for j in plan.joins:
        if j.right not in aliases:
            aliases[j.right] = f"t{len(aliases)}"
            joins_sql.append(f"JOIN {q(j.right)} {aliases[j.right]} ON {aliases[j.left]}.{q(j.left_col)} = "
                             f"{aliases[j.right]}.{q(j.right_col)}")

    def ref(coll: str, col: str) -> str:
        return f"{aliases[coll]}.{q(col)}"

    agg = plan.aggregation
    if agg == "COUNT":
        pk = [c.column for c in view.cols_of(plan.primary_collection) if c.is_pk]
        value = f"COUNT(DISTINCT {ref(plan.primary_collection, pk[0])})" if (pk and plan.joins) else "COUNT(*)"
    elif plan.measure:
        fn = {"TOP": "SUM", "LIST": "SUM"}.get(agg, agg)
        value = f"{fn}({ref(*plan.measure)})"
    else:
        value = "COUNT(*)"
    where = []
    for f in plan.filters:
        col = ref(f.collection, f.column)
        if f.op == "IS NULL":
            where.append(f"{col} IS NULL")
        elif f.op == "YEAR":
            where.append(f"EXTRACT(YEAR FROM {col}) = {int(f.value)}")
        elif f.op == "LIKE":
            where.append(f"CAST({col} AS VARCHAR) LIKE {_lit(f.value)}")
        else:
            where.append(f"{col} {f.op} {_lit(f.value)}")
    select = [f"{ref(*g)} AS {q(g[1])}" for g in plan.group_by] + [f"{value} AS value"]
    sql = f"SELECT {', '.join(select)} FROM {q(plan.primary_collection)} t0"
    if joins_sql:
        sql += " " + " ".join(joins_sql)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if plan.group_by:
        sql += " GROUP BY " + ", ".join(ref(*g) for g in plan.group_by)
        sql += f" ORDER BY value {'DESC' if plan.order_desc else 'ASC'}"
    if plan.limit:
        sql += f" LIMIT {int(plan.limit)}"
    return sql


def _lit(v: Any) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def structured_value(plan: QueryPlan, rows: List[Dict[str, Any]]) -> Any:
    if not rows:
        return None
    if not plan.group_by:
        return rows[0].get("value")
    key = plan.group_by[0][1]
    return {str(r.get(key)): r.get("value") for r in rows}


def describe_answer(question: str, plan: QueryPlan, value: Any) -> str:
    if value is None:
        return "The query returned no matching data."
    unit = ""
    if plan.measure and "usd" in tokens(plan.measure[1]):
        unit = " USD"
    if isinstance(value, dict):
        items = list(value.items())[:15]
        body = "; ".join(f"{k}: {_num(v)}{unit}" for k, v in items)
        return f"{len(value)} groups - {body}"
    what = {"COUNT": "Count", "AVG": "Average", "MAX": "Maximum", "MIN": "Minimum"}.get(plan.aggregation, "Total")
    target = f" of {plan.measure[1]}" if plan.measure else ""
    return f"{what}{target}: {_num(value)}{unit}"


def _num(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) >= 1 or v == 0 else f"{v:.4f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)
