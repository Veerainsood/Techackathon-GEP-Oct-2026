"""Workspace sessions for uploaded files.

Uploads are analysed in a workspace-scoped namespace ``<tenant>::ws::<id>``: the files are registered as a
DuckDB-backed source visible only to that namespace, profiled and described there, and removed with the
workspace.  The tenant's persistent ontology is never modified by uploads.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.gateway.file_adapter import FileSourceAdapter, _safe_name
from backend.gateway.registry import AccessContext, AccessDenied


class WorkspaceManager:
    def __init__(self, engine: Any):
        self.e = engine
        self._ws: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def namespace(tenant: str, ws_id: str) -> str:
        return f"{tenant}::ws::{ws_id}"

    def create(self, ctx: AccessContext, file_ids: List[str]) -> Dict[str, Any]:
        if not file_ids:
            raise ValueError("a workspace needs at least one file")
        recs = [self.e.files.get(ctx.tenant_id, ctx.user_id, fid, kind="upload") for fid in file_ids]
        ws_id = uuid.uuid4().hex[:12]
        ns = self.namespace(ctx.tenant_id, ws_id)
        source_id = f"ws_{ws_id}"
        adapter = FileSourceAdapter(source_id, [str(self.e.files.path(r)) for r in recs], name=f"Workspace {ws_id}",
                                    description="Uploaded files (workspace-scoped, not part of the persistent ontology)")
        self.e.gateway.register(adapter, tenants=[ns])
        ws_ctx = AccessContext(tenant_id=ns, user_id=ctx.user_id, max_rows=ctx.max_rows)
        prep = self.e.prepare_source(ws_ctx, source_id, force=True)
        files = [{"file_id": r.file_id, "filename": r.filename, "sha256": r.sha256, "size": r.size,
                  "collection": _safe_name(Path(r.filename).stem)} for r in recs]
        info = {"workspace_id": ws_id, "tenant_id": ctx.tenant_id, "owner": ctx.user_id, "namespace": ns, "source_id": source_id,
                "files": files, "collections": adapter.discover_collections(),
                "profiling": {k: prep["ontology"].get(k) for k in ("collections", "columns", "verified_relationships")}}
        with self._lock:
            self._ws[ws_id] = info
        return info

    def get(self, ctx: AccessContext, ws_id: str) -> Dict[str, Any]:
        info = self._ws.get(ws_id)
        if not info or info["tenant_id"] != ctx.tenant_id or info["owner"] != ctx.user_id:
            raise AccessDenied("workspace not found for this user")
        return info

    def context(self, ctx: AccessContext, ws_id: str) -> AccessContext:
        info = self.get(ctx, ws_id)
        return AccessContext(tenant_id=info["namespace"], user_id=ctx.user_id, max_rows=ctx.max_rows,
                             manifest={"workspace_files": info["files"]}, principal_tenant=ctx.tenant_id)

    def list(self, ctx: AccessContext) -> List[Dict[str, Any]]:
        return [w for w in self._ws.values() if w["tenant_id"] == ctx.tenant_id and w["owner"] == ctx.user_id]

    def expire_missing(self) -> int:
        """Close workspaces whose files were removed by retention; returns how many were closed."""
        with self._lock:
            gone = [w for w in self._ws.values()
                    if any(not self.e.files._dir(w["tenant_id"], "upload", f["file_id"]).exists() for f in w["files"])]
        for w in gone:
            self._drop(w)
        return len(gone)

    def close(self, ctx: AccessContext, ws_id: str) -> None:
        self._drop(self.get(ctx, ws_id))

    def _drop(self, info: Dict[str, Any]) -> None:
        ws_id = info["workspace_id"]
        try:
            self.e.gateway.unregister(info["source_id"])
        except Exception:
            pass
        self.e.kg.delete_tenant(info["namespace"])
        if self.e.contexts:
            self.e.contexts.delete_tenant(info["namespace"])
        with self._lock:
            self._ws.pop(ws_id, None)
