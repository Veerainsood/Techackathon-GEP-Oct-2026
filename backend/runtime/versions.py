"""Source-version manifests and consistent snapshots for one analysis.

Every analysis resolves a manifest of committed versions before the agents start; both agents receive the
same manifest and the same AccessContext.  PostgreSQL sources additionally get an exported snapshot
(``pg_export_snapshot``) that every query of the analysis imports, so a sync committed mid-analysis is not
visible to either agent.  Other sources are checked at the end; if their version moved, the result carries
a freshness warning (and the workflow may retry).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.gateway.registry import AccessContext


def resolve_manifest(engine: Any, ctx: AccessContext, sources: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"resolved_at": datetime.now(timezone.utc).isoformat(), "tenant_id": ctx.tenant_id, "sources": {}}
    for sid in sources:
        sv = (engine.runtime_store.source_version(ctx.tenant_id, sid) if engine.runtime_store else None) or {}
        adapter = engine.gateway.adapter(ctx, sid)
        cur = engine.contexts.current(ctx.tenant_id, sid) if engine.contexts else None
        out["sources"][sid] = {
            "data_version": sv.get("data_version"), "sequence": sv.get("sequence"),
            "last_sync_at": sv.get("last_sync_at").isoformat() if sv.get("last_sync_at") else None,
            "sync_status": sv.get("sync_status", "unknown"), "schema_version": engine.kg.source_version(sid, ctx.tenant_id),
            "knowledge_version": cur["version"] if cur else None, "mode": adapter.provenance().get("mode"),
            "consistency": "pg_snapshot" if hasattr(adapter, "export_snapshot") else "detect_only",
        }
    out["digest"] = hashlib.sha1(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "last_sync_at"}
                                             for k, v in out["sources"].items()}, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return out


class SnapshotLeases:
    """Holds exported PostgreSQL snapshots open for the duration of one analysis."""

    def __init__(self) -> None:
        self._holders: List[Any] = []

    def open(self, engine: Any, ctx: AccessContext, manifest: Dict[str, Any]) -> Dict[str, str]:
        for sid, m in manifest["sources"].items():
            if m["consistency"] != "pg_snapshot":
                continue
            try:
                snap, holder = engine.gateway.adapter(ctx, sid).export_snapshot()
            except Exception as exc:  # degrade to detection, never block the analysis
                m["consistency"], m["snapshot_error"] = "detect_only", str(exc)[:200]
                continue
            ctx.snapshots[sid] = snap
            m["snapshot_id"] = snap
            self._holders.append(holder)
        return dict(ctx.snapshots)

    def close(self) -> None:
        for h in self._holders:
            try:
                h.rollback()
                h.close()
            except Exception:
                pass
        self._holders.clear()


def check_consistency(engine: Any, ctx: AccessContext, manifest: Dict[str, Any]) -> Dict[str, Any]:
    warnings, moved = [], []
    for sid, m in manifest.get("sources", {}).items():
        sv = (engine.runtime_store.source_version(ctx.tenant_id, sid) if engine.runtime_store else None) or {}
        if sv.get("sequence") != m.get("sequence") or sv.get("data_version") != m.get("data_version"):
            moved.append(sid)
            if m["consistency"] == "pg_snapshot" and m.get("snapshot_id"):
                warnings.append(f"{sid} advanced to {sv.get('data_version')} during the analysis; results are consistent with "
                                f"{m.get('data_version') or 'the snapshot taken at start'} (exported snapshot) and may now be outdated")
            else:
                warnings.append(f"{sid} advanced to {sv.get('data_version')} during the analysis; queries may mix "
                                f"{m.get('data_version')} and newer data")
        if m.get("sync_status") not in (None, "ok", "unknown"):
            warnings.append(f"{sid} last sync status is {m['sync_status']}; data may be stale")
    status = "consistent" if not moved else ("snapshot_consistent_but_outdated" if all(
        manifest["sources"][s]["consistency"] == "pg_snapshot" and manifest["sources"][s].get("snapshot_id") for s in moved)
        else "changed_during_run")
    return {"status": status, "moved_sources": moved, "warnings": warnings}
