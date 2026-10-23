"""Reusable ingestion lifecycle for API connectors.

REGISTER -> AUTHENTICATE -> DISCOVER -> FETCH/NORMALIZE (per object, checkpointed) -> DERIVED TABLES
-> PROFILE + ONTOLOGY -> COLLECTION FILES + GLOBAL CONTEXT -> CROSS-SOURCE CANDIDATES -> QUERYABLE

A failing object is recorded and skipped; its checkpoint is not advanced, so the next run resumes
from the last committed state.  Authentication failures stop the run before any data is touched.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from udre.gateway.registry import AccessContext
from udre.ingestion.base import AuthenticationError, SourceConnector, snake
from udre.ingestion.snapshot import SnapshotSourceAdapter, SnapshotStore

SNAPSHOT_ROOT = Path(os.environ.get("UDRE_SNAPSHOT_DIR", Path(__file__).resolve().parents[2] / ".udre_snapshots"))


@dataclass
class IngestionReport:
    source_id: str
    connector: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    objects: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    failed_objects: Dict[str, str] = field(default_factory=dict)
    ok: bool = True

    def step(self, name: str, t0: float, status: str = "ok", **detail: Any) -> None:
        self.steps.append({"step": name, "status": status, "ms": round((time.perf_counter() - t0) * 1000, 1), **detail})


class IngestionPipeline:
    def __init__(self, engine: Any, snapshot_root: Optional[Path] = None):
        self.e = engine
        self.root = Path(snapshot_root or SNAPSHOT_ROOT)
        self.stores: Dict[str, SnapshotStore] = {}
        self.connectors: Dict[str, SourceConnector] = {}

    def _store(self, tenant: str, source_id: str) -> SnapshotStore:
        key = f"{tenant}/{source_id}"
        if key not in self.stores:
            self.stores[key] = SnapshotStore(str(self.root / tenant / f"{source_id}.duckdb"))
        return self.stores[key]

    def ingest(self, ctx: AccessContext, connector: SourceConnector, source_id: str, full: bool = False,
               tenants: Optional[List[str]] = None, name: str = "") -> IngestionReport:
        rep = IngestionReport(source_id=source_id, connector=type(connector).__name__)
        caps = connector.capabilities()
        # REGISTER
        t0 = time.perf_counter()
        store = self._store(ctx.tenant_id, source_id)
        if source_id not in [s.source_id for s in self.e.gateway.sources(ctx)]:
            adapter = SnapshotSourceAdapter(source_id, store, connector.source_type, name=name or source_id,
                                            description=f"Synchronized snapshot of {connector.source_type.value} ({caps.api_version})",
                                            connector_name=connector.source_type.value)
            self.e.gateway.register(adapter, tenants=tenants or [ctx.tenant_id])
        self.connectors[source_id] = connector
        rep.step("register", t0, mode="synchronized_snapshot", live_query=caps.live_query)
        # AUTHENTICATE
        t0 = time.perf_counter()
        try:
            connector.authenticate()
        except AuthenticationError as exc:
            rep.ok = False
            rep.step("authenticate", t0, "failed", error=str(exc))
            return rep
        rep.step("authenticate", t0)
        # DISCOVER
        t0 = time.perf_counter()
        specs = connector.discover_objects()
        rep.step("discover", t0, objects=[s.name for s in specs])
        # FETCH / NORMALIZE / STORE
        ids: Dict[str, List[str]] = {}
        changed = 0
        for spec in specs:
            t0 = time.perf_counter()
            state = store.state(spec.name)
            use_full = full or spec.incremental == "none" or not state or not state.get("checkpoint")
            if hasattr(connector, "pending_ids"):  # replay persisted webhook change queue (survives restarts)
                queued = [c["object_id"] for c in store.pending_changes(spec.name)]
                if queued:
                    connector.pending_ids.setdefault(spec.name, [])
                    connector.pending_ids[spec.name] = list(dict.fromkeys(connector.pending_ids[spec.name] + queued))
            try:
                res = connector.fetch(spec, None if use_full else state["checkpoint"], full=use_full)
                stats = store.upsert(spec, res.records, res.mode, res.checkpoint, res.archived_ids, spec.field_meta)
                store.mark_changes_processed(spec.name)
                changed += stats["upserted"] + stats["archived"]
                with store.lock:
                    pk = snake(spec.primary_key)
                    rows = store.conn.execute(f'SELECT "{pk}" FROM "{spec.name}"').fetchall() if spec.name in store.tables() else []
                ids[spec.name] = [r[0] for r in rows]
                rep.objects[spec.name] = {"mode": res.mode, "pages": res.pages, **stats, "checkpoint": res.checkpoint}
                rep.step(f"fetch:{spec.name}", t0, mode=res.mode, upserted=stats["upserted"])
            except Exception as exc:  # recorded; other objects continue, checkpoint unchanged
                store.record_failure(spec.name, str(exc))
                rep.failed_objects[spec.name] = str(exc)[:300]
                rep.step(f"fetch:{spec.name}", t0, "failed", error=str(exc)[:300])
        # DERIVED TABLES (associations, pipelines ...)
        t0 = time.perf_counter()
        try:
            extra = connector.extra_tables(specs, ids)
            for table, rows in extra.items():
                store.replace_table(table, rows)
            store.set_relationships(connector.relationship_hints(specs))
            rep.step("derived_tables", t0, tables={k: len(v) for k, v in extra.items()})
        except Exception as exc:
            rep.failed_objects["derived_tables"] = str(exc)[:300]
            rep.step("derived_tables", t0, "failed", error=str(exc)[:300])
        adapter = self.e.gateway.adapter(ctx, source_id)
        if hasattr(adapter, "refresh"):
            adapter.refresh()
        # PROFILE + ONTOLOGY + COLLECTION FILES + GLOBAL CONTEXT
        t0 = time.perf_counter()
        prep = self.e.prepare_source(ctx, source_id, force=changed > 0 or full)
        rep.step("ontology_and_skill_files", t0, ontology={k: prep["ontology"][k] for k in ("collections", "columns",
                                                                                            "declared_relationships", "verified_relationships")},
                 context_version=(prep.get("contexts") or {}).get("version"))
        # CROSS-SOURCE CANDIDATES (never asserted as identity)
        t0 = time.perf_counter()
        if getattr(self.e, "cross_source", None):
            found = self.e.cross_source.discover(ctx, focus_source=source_id)
            rep.step("cross_source_candidates", t0, candidates=len(found))
            for sid in sorted({p[k] for p in found for k in ("left_source", "right_source")}):
                self.e.contexts.generate(ctx, sid)  # both sides' reference files list the new candidates
        # QUERYABLE
        t0 = time.perf_counter()
        probe = self.e.gateway.execute_query(ctx, source_id, f'SELECT COUNT(*) AS n FROM "{specs[0].name}"') if specs else None
        rep.step("queryable", t0, "ok" if probe is None or probe.succeeded else "failed",
                 provenance=(probe.provenance if probe else {}))
        rep.ok = not rep.failed_objects
        return rep

    def sync(self, ctx: AccessContext, source_id: str, full: bool = False) -> IngestionReport:
        if source_id not in self.connectors:
            raise KeyError(f"no connector registered for {source_id}")
        return self.ingest(ctx, self.connectors[source_id], source_id, full=full)
