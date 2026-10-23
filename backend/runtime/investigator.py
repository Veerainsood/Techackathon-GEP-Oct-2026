"""Scoped change investigation for synchronization events.

Stage A (always, deterministic, cheap):
  * only the collections named in the event are queried; dependents are reached through verified
    relationships, and only with bounded key-filtered queries
  * changed keys / CDC operations are used when present; otherwise the half-open window
    [window_start, window_end) on the mapped timestamp column, plus a lookback margin
  * visibility: if the producer's expected changes are not yet readable, the event is deferred, not skipped
  * before/after column changes are reported only when the producer supplied ``before`` images;
    timestamp-only events never claim historical deltas
  * schema changes refresh knowledge; data-only changes refresh the changed collections' profiles,
    regenerate only their reference files, invalidate compiled context and mark affected answers stale
Stage B (policy-gated): watched analyses whose collections were affected, anomalies, or unresolved
relationships may run the agents.  Disabled by default.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.contracts.models import EdgeType, NodeType
from backend.events.contracts import CollectionChange, SyncEvent
from backend.events.store import RuntimeStore
from backend.gateway.base import type_family
from backend.gateway.registry import AccessContext, AccessDenied
from backend.runtime.policy import RuntimePolicy

TOMBSTONE_COLUMNS = ("_backend_archived", "is_deleted", "deleted", "archived", "deleted_at", "is_archived")


def _bind(dt: datetime, column_type: str) -> datetime:
    """Event timestamps are UTC. Columns without a time zone are compared with naive UTC values so the database
    never applies its session time zone."""
    t = (column_type or "").lower()
    with_tz = "with time zone" in t or "timestamptz" in t or "time zone" in t
    if with_tz:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


class NotYetVisible(RuntimeError):
    """The producer's writes for this event are not readable yet; retry later."""


class EventRejected(ValueError):
    pass


@dataclass
class CollectionFinding:
    name: str
    status: str = "ok"                     # ok | unverifiable_window | missing_collection | failed_in_source
    method: str = ""                       # cdc_keys | timestamp_window | count_only
    timestamp_column: Optional[str] = None
    changed_rows: Optional[int] = None
    late_rows: Optional[int] = None
    operations: Dict[str, int] = field(default_factory=dict)
    deleted_rows: Optional[int] = None
    changed_columns: Optional[List[str]] = None
    changed_keys_sample: List[Any] = field(default_factory=list)
    affected_parents: List[Dict[str, Any]] = field(default_factory=list)
    affected_dependents: List[Dict[str, Any]] = field(default_factory=list)
    affected_metrics: List[str] = field(default_factory=list)
    schema_changed: bool = False
    watermark: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)


class ChangeInvestigator:
    def __init__(self, engine: Any, store: RuntimeStore, policy: Optional[RuntimePolicy] = None):
        self.e = engine
        self.store = store
        self.policy = policy or RuntimePolicy.load()

    # ------------------------------------------------------------------ helpers
    def _ctx(self, ev: SyncEvent) -> AccessContext:
        return AccessContext(tenant_id=ev.tenant_id, user_id="backend-runtime", max_rows=self.policy.max_keys + 1)

    def _q(self, ctx, source_id: str, sql: str, params: Sequence[Any], finding: CollectionFinding) -> List[Dict[str, Any]]:
        res = self.e.gateway.execute_query(ctx, source_id, sql, params=list(params))
        finding.queries.append(sql)
        if not res.succeeded:
            raise RuntimeError(f"query failed: {res.error_message}")
        return res.rows

    # ------------------------------------------------------------------ entry
    def process(self, ev: SyncEvent) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(ev)
        try:
            adapter = self.e.gateway.adapter(ctx, ev.source_id)
        except AccessDenied as exc:
            raise EventRejected(f"source {ev.source_id} is not registered for tenant {ev.tenant_id}") from exc
        prev = self.store.source_version(ev.tenant_id, ev.source_id) or {}
        notes: List[str] = []
        if prev.get("sequence") is not None and ev.sequence is not None:
            if ev.sequence <= prev["sequence"]:
                notes.append(f"out-of-order or replayed event: sequence {ev.sequence} <= current {prev['sequence']}; "
                             "window investigated, source version not regressed")
            elif ev.sequence > prev["sequence"] + 1:
                notes.append(f"sequence gap: expected {prev['sequence'] + 1}, got {ev.sequence}; missing versions not yet received")

        if ev.event_type == "knowledge.version.published":
            refreshed = self._refresh_knowledge(ctx, ev, schema_changed=True)
            self.store.advance_version(ev.tenant_id, ev.source_id, ev.data_version, ev.sequence, ev.event_id, ev.window_end,
                                       ev.schema_version, ev.knowledge_version or refreshed.get("knowledge_version"))
            return {"event_id": ev.event_id, "kind": "knowledge_refresh", "knowledge": refreshed, "notes": notes}

        if ev.event_type == "database.sync.failed" or ev.status == "failed":
            self.store.mark_sync_status(ev.tenant_id, ev.source_id, "sync_failed",
                                        "; ".join(c.error or "" for c in ev.collections if c.error) or ev.error
                                        or "producer reported failure")
            return {"event_id": ev.event_id, "kind": "sync_failed", "notes": notes + ["no investigation: sync did not complete; "
                                                                                     "watermarks and versions unchanged"]}

        known = set(self.e.gateway.collections(ctx, ev.source_id))
        if any(c.name not in known for c in ev.collections) and hasattr(adapter, "refresh"):
            adapter.refresh()  # the producer may have created the table after the catalog was cached
            before, known = known, set(self.e.gateway.collections(ctx, ev.source_id))
            created = known - before
        else:
            created = set()
        findings: List[CollectionFinding] = []
        for col in ev.collections:
            f = CollectionFinding(name=col.name)
            findings.append(f)
            if col.status != "completed":
                f.status, f.notes = "failed_in_source", [f"producer reported {col.status}: {col.error or ''}".strip()]
                continue
            if col.name not in known:
                f.status, f.notes = "missing_collection", [f"collection {col.name} is not visible in source {ev.source_id}"]
                continue
            self._investigate_collection(ctx, adapter, ev, col, f)
            if col.name in created:
                f.schema_changed = True
                f.notes.append("new collection: first seen in this event")

        schema_changed = any(f.schema_changed for f in findings) or bool(
            ev.schema_version and prev.get("schema_version") and ev.schema_version != prev.get("schema_version"))
        changed = [f.name for f in findings if f.status == "ok" and (f.changed_rows or f.deleted_rows or f.operations)]
        dependents = sorted({d["collection"] for f in findings for d in f.affected_dependents if d.get("affected_rows")})
        knowledge = self._refresh_knowledge(ctx, ev, schema_changed, [f.name for f in findings if f.status == "ok"], ev.data_version)
        stale = self.store.mark_answers_stale(ev.tenant_id, ev.source_id, changed + dependents,
                                              f"{ev.data_version}: changes in {', '.join(changed + dependents)}") if changed else 0
        if changed:
            stale += self._propagate_to_siblings(ctx, ev, adapter, changed + dependents, notes)
        for f in findings:  # watermarks only advance for collections verified through a window or keys
            if f.status == "ok":
                f.watermark = self.store.cover(ev.tenant_id, ev.source_id, f.name, ev.window_start, ev.window_end, ev.event_id)
                if f.watermark["gaps"]:
                    f.notes.append(f"uncovered windows remain: {f.watermark['gaps']}")
        moved = self.store.advance_version(ev.tenant_id, ev.source_id, ev.data_version, ev.sequence, ev.event_id, ev.window_end,
                                           ev.schema_version, knowledge.get("knowledge_version"))
        summary = {
            "event_id": ev.event_id, "kind": "data_change", "source_id": ev.source_id, "data_version": ev.data_version,
            "window": [ev.window_start.isoformat(), ev.window_end.isoformat()], "interval": "half-open [start, end)",
            "lookback_s": self.policy.lookback_s, "schema_changed": schema_changed, "changed_collections": changed,
            "dependent_collections": dependents, "answers_marked_stale": stale, "version_advanced": moved,
            "knowledge": knowledge, "notes": notes,
            "collections": [f.__dict__ for f in findings], "ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        summary["stage_b"] = self._stage_b(ctx, ev, summary)
        return summary

    # ------------------------------------------------------------------ per collection
    def _investigate_collection(self, ctx, adapter, ev: SyncEvent, col: CollectionChange, f: CollectionFinding) -> None:
        schema = adapter.describe_collection(col.name)
        cols = {c.name: c for c in schema.columns}
        q, ph = adapter.quote, adapter.placeholder
        t = q(col.name)
        pk = schema.primary_keys[0] if schema.primary_keys else None
        f.schema_changed = col.schema_changed or self._schema_drift(ctx, ev.source_id, col.name, schema)
        tomb = next((c for c in TOMBSTONE_COLUMNS if c in cols), None)

        if not col.changes and col.expected_changes == 0:
            f.method, f.changed_rows = "producer_reported_no_changes", 0
            f.notes.append("producer reported zero changes for this collection in the window")
            return

        if col.changes and pk:
            f.method = "cdc_keys"
            for ch in col.changes:
                f.operations[ch.op] = f.operations.get(ch.op, 0) + 1
            live_ops = [c for c in col.changes if c.op in ("insert", "update")]
            keys = [str(c.key.get(pk)) for c in live_ops][: self.policy.max_keys]
            if keys:
                rows = self._q(ctx, ev.source_id, f"SELECT {q(pk)} AS k FROM {t} WHERE CAST({q(pk)} AS VARCHAR) IN "
                               f"({', '.join([ph] * len(keys))})", keys, f)
                visible = {str(r["k"]) for r in rows}
                missing = [k for k in keys if k not in visible]
                if missing:
                    raise NotYetVisible(f"{col.name}: {len(missing)} inserted/updated keys not readable yet (e.g. {missing[:3]})")
            dels = [str(c.key.get(pk)) for c in col.changes if c.op == "delete"][: self.policy.max_keys]
            if dels:
                where = f"CAST({q(pk)} AS VARCHAR) IN ({', '.join([ph] * len(dels))})"
                if tomb:
                    flag = f"{q(tomb)} IS NOT NULL" if tomb.endswith("_at") else f"{q(tomb)} = TRUE"
                    rows = self._q(ctx, ev.source_id, f"SELECT COUNT(*) AS n FROM {t} WHERE {where} AND NOT ({flag})", dels, f)
                else:
                    rows = self._q(ctx, ev.source_id, f"SELECT COUNT(*) AS n FROM {t} WHERE {where}", dels, f)
                if rows[0]["n"]:
                    raise NotYetVisible(f"{col.name}: {rows[0]['n']} deleted keys still present")
                f.deleted_rows = len(dels)
            f.changed_rows = len(live_ops)
            f.changed_keys_sample = keys[:20] + dels[:20]
            befores = [c for c in col.changes if c.before]
            if befores:
                f.changed_columns = sorted({k for c in befores for k in (c.changed_columns or c.before.keys())})
            elif any(c.changed_columns for c in col.changes):
                f.changed_columns = sorted({k for c in col.changes for k in (c.changed_columns or [])})
            else:
                f.notes.append("no before-images supplied: changed columns cannot be determined")
            key_filter = (f"CAST({q(pk)} AS VARCHAR) IN ({', '.join([ph] * len(keys))})", keys) if keys else None
        else:
            ts = self.policy.timestamp_column(ev.source_id, col.name, col.timestamp_column)
            if not ts or ts not in cols:
                f.status, f.method = "unverifiable_window", "count_only"
                f.notes.append(f"timestamp column {ts!r} not found in {col.name}; window cannot be verified "
                               "(provide changed keys or a valid timestamp mapping); watermark not advanced")
                f.changed_rows = None
                return
            fam = type_family(cols[ts].data_type)
            if fam != "temporal":
                f.status, f.method = "unverifiable_window", "count_only"
                f.notes.append(f"timestamp column {ts} has non-temporal type {cols[ts].data_type}")
                return
            f.method, f.timestamp_column = "timestamp_window", ts
            start, end = _bind(ev.window_start, cols[ts].data_type), _bind(ev.window_end, cols[ts].data_type)
            look = start - timedelta(seconds=self.policy.lookback_s)
            tsq = q(ts)
            row = self._q(ctx, ev.source_id,
                          f"SELECT SUM(CASE WHEN {tsq} >= {ph} THEN 1 ELSE 0 END) AS in_window, "
                          f"SUM(CASE WHEN {tsq} < {ph} THEN 1 ELSE 0 END) AS late, MIN({tsq}) AS min_ts, MAX({tsq}) AS max_ts "
                          f"FROM {t} WHERE {tsq} >= {ph} AND {tsq} < {ph}", [start, start, look, end], f)[0]
            f.changed_rows, f.late_rows = int(row["in_window"] or 0), int(row["late"] or 0)
            if f.late_rows:
                f.notes.append(f"{f.late_rows} rows modified in the {self.policy.lookback_s}s before the window: possible late "
                               "arrivals, included in impact analysis (may overlap a previous event)")
            if col.expected_changes is not None and f.changed_rows < col.expected_changes:
                raise NotYetVisible(f"{col.name}: {f.changed_rows} rows readable in window, producer reported {col.expected_changes}")
            if tomb:
                flag = f"{q(tomb)} IS NOT NULL" if tomb.endswith("_at") else f"{q(tomb)} = TRUE"
                f.deleted_rows = int(self._q(ctx, ev.source_id, f"SELECT COUNT(*) AS n FROM {t} WHERE {tsq} >= {ph} AND {tsq} < {ph} "
                                                                f"AND {flag}", [look, end], f)[0]["n"] or 0)
            else:
                f.notes.append("hard deletes are not observable from a timestamp window (no tombstone column, no CDC keys)")
            f.notes.append("timestamp-only event: before/after values are not available, only current state is reported")
            if pk:
                rows = self._q(ctx, ev.source_id, f"SELECT {q(pk)} AS k FROM {t} WHERE {tsq} >= {ph} AND {tsq} < {ph} "
                                                  f"ORDER BY {q(pk)} LIMIT {self.policy.max_keys}", [look, end], f)
                keys = [str(r["k"]) for r in rows]
                f.changed_keys_sample = keys[:20]
                key_filter = (f"CAST({q(pk)} AS VARCHAR) IN ({', '.join([ph] * len(keys))})", keys) if keys else None
            else:
                key_filter = (f"{tsq} >= {ph} AND {tsq} < {ph}", [look, end])
        if key_filter:
            self._impact(ctx, adapter, ev.source_id, col.name, schema, key_filter, f)

    def _schema_drift(self, ctx, source_id: str, collection: str, schema) -> bool:
        node = self.e.kg.get_node(f"coll:{source_id}:{collection}", ctx.tenant_id)
        if not node:
            return True
        return sorted(node["properties"].get("columns", [])) != sorted(c.name for c in schema.columns)

    # ------------------------------------------------------------------ impact through verified relationships
    def _impact(self, ctx, adapter, source_id: str, coll: str, schema, key_filter: Tuple[str, List[Any]], f: CollectionFinding) -> None:
        q, ph = adapter.quote, adapter.placeholder
        where, params = key_filter
        if not params:
            return
        refs = [e for e in self.e.kg.edges(EdgeType.REFERENCES, source_id=source_id, tenant=ctx.tenant_id)
                if e["verification_status"] == "verified_fact"]
        for e in refs:
            p = e["properties"]
            if p["child"] == coll:  # changed rows point to parent entities
                rows = self._q(ctx, source_id, f"SELECT CAST({q(p['child_col'])} AS VARCHAR) AS k, COUNT(*) AS n FROM {q(coll)} "
                                               f"WHERE {where} AND {q(p['child_col'])} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 20",
                               params, f)
                if rows:
                    f.affected_parents.append({"collection": p["parent"], "via": p["child_col"], "entities": len(rows),
                                               "sample_keys": [r["k"] for r in rows[:10]]})
            elif p["parent"] == coll and schema.primary_keys and p["parent_col"] == schema.primary_keys[0]:
                keys = params if where.startswith("CAST(") else None
                if keys is None:
                    continue
                child_where = f"CAST({q(p['child_col'])} AS VARCHAR) IN ({', '.join([ph] * len(keys))})"
                n = self._q(ctx, source_id, f"SELECT COUNT(*) AS n FROM {q(p['child'])} WHERE {child_where}", keys, f)[0]["n"]
                f.affected_dependents.append({"collection": p["child"], "via": p["child_col"], "affected_rows": int(n or 0),
                                              "reason": f"references changed {coll} rows (verified relationship)"})
        for e in self.e.kg.edges(EdgeType.DERIVED_FROM, source_id=source_id, tenant=ctx.tenant_id):
            r = e["properties"]
            if r.get("rule") and coll in (r["parent"], r["child"]):
                other = r["parent"] if r["child"] == coll else r["child"]
                f.affected_dependents.append({"collection": other, "via": f"{r['parent']}.{r['parent_col']} = SUM({r['child']}.{r['child_col']})",
                                              "affected_rows": None, "reason": "reconciliation rule may need re-checking"})
        f.affected_metrics = sorted({m["label"] + f" ({m['properties']['collection']}.{m['properties']['column']})"
                                     for m in self.e.kg.nodes(NodeType.METRIC, source_id, ctx.tenant_id)
                                     if m["properties"].get("collection") in {coll} | {d["collection"] for d in f.affected_dependents}})

    # ------------------------------------------------------------------ knowledge / caches
    def _propagate_to_siblings(self, ctx, ev: SyncEvent, adapter, collections: Sequence[str], notes: List[str]) -> int:
        """Other sources reading the same database see the same rows: flag their answers and refresh their caches."""
        uri = getattr(adapter, "uri", None)
        if not uri:
            return 0
        stale = 0
        for s in self.e.gateway.sources(ctx):
            if s.source_id == ev.source_id or getattr(self.e.gateway.adapter(ctx, s.source_id), "uri", None) != uri:
                continue
            shared = [c for c in collections if c in set(self.e.gateway.collections(ctx, s.source_id))]
            if not shared:
                continue
            stale += self.store.mark_answers_stale(ev.tenant_id, s.source_id, shared, f"{ev.data_version} (via {ev.source_id}): "
                                                   f"changes in {', '.join(shared)}")
            self.e.invalidate_caches(ctx.tenant_id, s.source_id)
            try:
                self.e.refresh_collection_profiles(ctx, s.source_id, shared)
            except Exception as exc:  # the sibling's knowledge is refreshed on its next rebuild
                notes.append(f"profile refresh for {s.source_id} failed: {type(exc).__name__}")
            notes.append(f"shared with source {s.source_id}: {', '.join(shared)} (answers flagged, caches refreshed)")
        return stale

    def _refresh_knowledge(self, ctx, ev: SyncEvent, schema_changed: bool, collections: Sequence[str] = (),
                           data_version: Optional[str] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {"action": None}
        self.e.invalidate_caches(ctx.tenant_id, ev.source_id)
        if schema_changed:
            prep = self.e.prepare_source(ctx, ev.source_id, force=True)  # rebuild ontology; stale facts marked by version change
            out.update(action="ontology_rebuilt", schema_version=prep["ontology"]["schema_version"],
                       knowledge_version=(prep.get("contexts") or {}).get("version"))
        elif collections:
            refreshed = self.e.refresh_collection_profiles(ctx, ev.source_id, list(collections))
            gen = self.e.contexts.generate(ctx, ev.source_id, only_collections=list(collections), data_version=data_version) \
                if self.e.contexts else None
            out.update(action="profiles_refreshed", collections=refreshed, ontology="unchanged (data-only change)",
                       knowledge_version=gen.version if gen else None)
        return out

    # ------------------------------------------------------------------ Stage B
    def _stage_b(self, ctx, ev: SyncEvent, summary: Dict[str, Any]) -> Dict[str, Any]:
        affected = set(summary["changed_collections"]) | set(summary["dependent_collections"])
        triggers: List[Dict[str, Any]] = []
        for w in self.policy.watched:
            if ev.source_id in w.sources and (not w.collections or affected & set(w.collections)):
                triggers.append({"type": "watched_analysis", "analysis_id": w.analysis_id, "question": w.question, "mode": w.mode})
        history = [i for i in self.store.investigations(ev.tenant_id, ev.source_id, limit=20) if i["status"] == "processed"]
        for c in summary["collections"]:
            prior = [x["changed_rows"] for i in history for x in i["summary"].get("collections", [])
                     if x["name"] == c["name"] and x.get("changed_rows") is not None]
            if c.get("changed_rows") and prior and c["changed_rows"] >= self.policy.anomaly_min_rows and \
                    c["changed_rows"] > self.policy.anomaly_ratio * (sum(prior) / len(prior)):
                triggers.append({"type": "anomaly", "collection": c["name"], "changed_rows": c["changed_rows"],
                                 "trailing_avg": round(sum(prior) / len(prior), 1)})
            if self.policy.llm_on_unresolved_relationships and c["status"] == "ok" and not c["affected_parents"] and not c["affected_dependents"]:
                triggers.append({"type": "unresolved_relationships", "collection": c["name"]})
        if not triggers:
            return {"ran": False, "reason": "no trigger (ordinary data change)"}
        if not self.policy.auto_analysis:
            return {"ran": False, "reason": "automatic analysis disabled by policy", "triggers": triggers}
        runs = []
        for trig in triggers:
            if trig["type"] != "watched_analysis":
                runs.append({"trigger": trig, "skipped": "no configured question for this trigger type"})
                continue
            state = self.e.ask(trig["question"], ctx=AccessContext(tenant_id=ev.tenant_id, user_id="backend-runtime",
                                                                     allowed_sources=set([ev.source_id])),
                               mode=trig["mode"], use_memory=False)
            d = state["decision"]
            runs.append({"trigger": trig, "answer": d["final_answer"], "status": d["verification_status"],
                         "path": d["selected_path"], "manifest": state.get("manifest")})
        return {"ran": True, "triggers": triggers, "runs": runs}
