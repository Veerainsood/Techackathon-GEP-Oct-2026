"""FastAPI service.

Run: uvicorn udre.api.app:app --port 8000
Identity is taken from ``X-Tenant-Id`` / ``X-User-Id`` headers (replace with real auth in production).
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from udre.config import ROOT
from udre.engine import Engine, build_engine
from udre.gateway.registry import AccessContext, AccessDenied
from udre.skills.tools import ToolBox

import os

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Universal Data Reasoning Engine", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("UDRE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])


@lru_cache(maxsize=1)
def engine() -> Engine:
    e = build_engine()
    if os.environ.get("UDRE_EVENT_WORKER", "1") == "1":
        e.consumer.start(poll_s=float(os.environ.get("UDRE_EVENT_POLL_S", "1.0")))
    return e


def result_payload(state: Dict[str, Any], question: str, include_candidates: bool = False) -> Dict[str, Any]:
    d = state["decision"]
    out = {"question": question, "request_id": state.get("request_id"), "answer": d["final_answer"],
           "verification_status": d["verification_status"], "selected_path": d["selected_path"],
           "agreement_status": d["agreement_status"], "confidence_score": d["confidence_score"], "data": d["final_structured_data"],
           "query": d["winning_query"], "source_id": d["winning_source_id"], "query_lineage": d["query_lineage"],
           "disagreement_analysis": d.get("disagreement_analysis"), "assumptions": d["unresolved_assumptions"],
           "evidence": [{"id": e["evidence_id"], "type": e["evidence_type"], "action": e["query_or_action"],
                         "observed": e["observed_summary"]} for e in d["supporting_evidence"]],
           "validation": [{"validator": f["validator_name"], "passed": f["passed"], "severity": f["severity"],
                           "message": f["message"]} for f in d["validation_findings"]],
           "adjudicator": d.get("llm_verdict") or {}, "verification_actions": d.get("verification_actions") or [],
           "manifest": state.get("manifest"), "consistency": state.get("consistency"), "answer_id": state.get("answer_id"),
           "artifacts": state.get("artifacts", []), "exports": state.get("exports", []), "trace": state["trace"],
           "total_ms": state["total_ms"], "retried_from": state.get("retried_from"),
           "candidates_summary": {c["agent_name"]: {"mode": c["reasoning_mode"], "value": c["structured_value"], "sql": c["query_or_plan"],
                                                    "interpretation": c["interpreted_question"], "tool_calls": c["tool_calls_count"],
                                                    "llm_usage": c.get("llm_usage"), "failure": c.get("failure"),
                                                    "provenance": c.get("result_provenance")}
                                  for c in (state.get("explorer_candidate"), state.get("hercules_candidate")) if c},
           "errors": {k: state[k] for k in ("explorer_error", "hercules_error") if state.get(k)}}
    if include_candidates:
        out["candidates"] = {k: state.get(k) for k in ("explorer_candidate", "hercules_candidate")}
    return out


def access(x_tenant_id: str = Header("default"), x_user_id: str = Header("anonymous")) -> AccessContext:
    return AccessContext(tenant_id=x_tenant_id, user_id=x_user_id, max_rows=engine().settings.max_rows)


class AskRequest(BaseModel):
    question: str
    mode: str = Field("dual", pattern="^(dual|explorer|hercules)$")
    use_memory: bool = True
    export_formats: List[str] = Field(default_factory=list)
    include_candidates: bool = False
    workspace_id: Optional[str] = None
    sources: Optional[List[str]] = None  # restrict the analysis to these authorized sources


class QueryRequest(BaseModel):
    source_id: str
    query: str
    max_rows: int = 200


class ExportRequest(BaseModel):
    rows: List[Dict[str, Any]]
    format: str = "csv"
    name: str = "result"


@app.get("/health")
def health() -> Dict[str, Any]:
    e = engine()
    return {"status": "ok", "sources": [s.source_id for s in e.gateway.sources(e.default_ctx)]}


def _scoped(req: "AskRequest", ctx: AccessContext) -> AccessContext:
    if req.sources:
        ctx.allowed_sources = set(req.sources)
    return ctx


@app.post("/ask")
def ask(req: AskRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        state = engine().ask(req.question, ctx=_scoped(req, ctx), mode=req.mode, use_memory=req.use_memory,
                             export_formats=req.export_formats, workspace_id=req.workspace_id)
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))
    return result_payload(state, req.question, req.include_candidates)


@app.get("/sources")
def sources(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    e = engine()
    return ToolBox(e.gateway, e.kg, None, e.settings.exports_dir).discover_sources(ctx)


@app.get("/sources/{source_id}/collections")
def collections(source_id: str, q: Optional[str] = None, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    e = engine()
    try:
        return ToolBox(e.gateway, e.kg, None, e.settings.exports_dir).discover_collections(ctx, query=q, source_id=source_id, k=50)
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))


@app.get("/sources/{source_id}/collections/{collection}")
def describe(source_id: str, collection: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    e = engine()
    try:
        return ToolBox(e.gateway, e.kg, None, e.settings.exports_dir).describe_collection(ctx, source_id, collection)
    except (AccessDenied, KeyError) as exc:
        raise HTTPException(404 if isinstance(exc, KeyError) else 403, str(exc))


@app.post("/query")
def query(req: QueryRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        return engine().gateway.execute_query(ctx, req.source_id, req.query, max_rows=req.max_rows).model_dump()
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))


@app.post("/sources/upload")
def upload(source_id: str, files: List[UploadFile] = File(...), ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    target = ROOT / "data" / "user_uploads" / ctx.tenant_id / source_id
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in files:
        name = Path(f.filename or "upload").name
        dest = target / name
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        paths.append(str(dest))
    return engine().register_files(source_id, paths, tenants=[ctx.tenant_id])


@app.post("/sources/{source_id}/refresh")
def refresh(source_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    e = engine()
    adapter = e.gateway.adapter(ctx, source_id)
    if hasattr(adapter, "refresh"):
        adapter.refresh()
    report = e.builder.build(ctx, source_id)
    if not report.skipped:
        e.memory.invalidate_source(source_id, report.schema_version)
    return report.__dict__


@app.get("/knowledge/stats")
def kg_stats(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    e = engine()
    return {"graph": e.kg.stats(ctx.tenant_id), "memory": e.memory.stats(ctx),
            "context": e.compiled_context(ctx).compression_stats.model_dump()}


@app.get("/knowledge/search")
def kg_search(q: str, k: int = 10, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return [{"id": n["node_id"], "type": n["node_type"], "label": n["label"], "status": n["verification_status"],
             "score": round(s, 3)} for n, s in engine().kg.search(q, k=k, tenant=ctx.tenant_id)]


@app.post("/ask/stream")
def ask_stream(req: AskRequest, ctx: AccessContext = Depends(access)):
    """Server-sent events: agent/tool events as they happen, then the final decision."""
    import json as _json
    import queue
    import threading

    from fastapi.responses import StreamingResponse

    q: "queue.Queue" = queue.Queue()

    def work():
        try:
            state = engine().ask(req.question, ctx=_scoped(req, ctx), mode=req.mode, use_memory=req.use_memory,
                                 export_formats=req.export_formats, workspace_id=req.workspace_id, on_event=q.put)
            q.put({"event": "result", "result": result_payload(state, req.question)})
        except Exception as exc:
            q.put({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield f"data: {_json.dumps(item, default=str)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/skills")
def skills(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().toolbox().list_skills(ctx)


@app.get("/skills/{skill_id}")
def skill(skill_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        return engine().toolbox().load_skill(ctx, skill_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.get("/sources/{source_id}/contexts")
def contexts(source_id: str, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    try:
        return engine().toolbox().list_collection_contexts(ctx, source_id)
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))


@app.get("/sources/{source_id}/contexts/{collection}")
def collection_context(source_id: str, collection: str, value_profile: bool = False, ctx: AccessContext = Depends(access)):
    try:
        return engine().toolbox().load_collection_context(ctx, source_id, collection, value_profile)
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc))


class ConnectorRequest(BaseModel):
    source_id: str
    resources: Optional[List[str]] = None  # Intune resources / HubSpot objects; credentials come from the environment
    full: bool = False


@app.post("/connectors/intune")
def connect_intune(req: ConnectorRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from udre.ingestion.base import AuthenticationError
    from udre.ingestion.intune import MicrosoftIntuneSourceAdapter

    try:
        conn = MicrosoftIntuneSourceAdapter(resources=req.resources)
    except AuthenticationError as exc:
        raise HTTPException(400, str(exc))
    return engine().ingestion.ingest(ctx, conn, req.source_id, full=req.full).__dict__


@app.post("/connectors/hubspot")
def connect_hubspot(req: ConnectorRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from udre.ingestion.base import AuthenticationError
    from udre.ingestion.hubspot import HubSpotSourceAdapter

    try:
        conn = HubSpotSourceAdapter(objects=req.resources)
    except AuthenticationError as exc:
        raise HTTPException(400, str(exc))
    return engine().ingestion.ingest(ctx, conn, req.source_id, full=req.full).__dict__


@app.post("/sources/{source_id}/sync")
def sync(source_id: str, full: bool = False, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        return engine().ingestion.sync(ctx, source_id, full=full).__dict__
    except KeyError as exc:
        raise HTTPException(404, str(exc))


@app.post("/webhooks/hubspot/{source_id}")
async def hubspot_webhook(source_id: str, request: Request) -> Dict[str, Any]:
    import os

    from udre.ingestion.hubspot import verify_webhook_v3

    body = (await request.body()).decode()
    secret = os.environ.get("UDRE_HUBSPOT_CLIENT_SECRET", "")
    ok = secret and verify_webhook_v3(secret, request.method, str(request.url), body,
                                      request.headers.get("X-HubSpot-Request-Timestamp", ""),
                                      request.headers.get("X-HubSpot-Signature-v3", ""))
    if not ok:
        raise HTTPException(401, "invalid webhook signature")
    conn = engine().ingestion.connectors.get(source_id)
    if conn is None:
        raise HTTPException(404, "unknown source")
    import json as _json

    stores = [s for k, s in engine().ingestion.stores.items() if k.endswith(f"/{source_id}")]
    return {"queued": conn.handle_webhook(_json.loads(body), stores[0] if stores else None)}


# ============================================================================ runtime: sync events and freshness
@app.post("/events/sync")
async def sync_event(request: Request, process: bool = False) -> Dict[str, Any]:
    """Ingress for the ingestion service. Body: udre.sync-event.v1 JSON; header X-UDRE-Signature: HMAC-SHA256 hex."""
    body = await request.body()
    res = engine().consumer.submit(body, request.headers.get("X-UDRE-Signature"))
    if res["status"] == "rejected":
        raise HTTPException(400 if "invalid event" in res.get("reason", "") else 401, res)
    if process and res["status"] == "accepted":
        res["processing"] = engine().consumer.drain(max_events=10)
    return res


@app.get("/runtime/sources")
def runtime_sources(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    e = engine()
    versions = {v["source_id"]: v for v in e.runtime_store.source_versions(ctx.tenant_id)}
    out = []
    for s in e.gateway.sources(ctx):
        adapter = e.gateway.adapter(ctx, s.source_id)
        cur = e.contexts.current(ctx.tenant_id, s.source_id) if e.contexts else None
        v = versions.get(s.source_id, {})
        prov = adapter.provenance()
        out.append({"source_id": s.source_id, "name": s.name, "type": s.source_type.value, "mode": prov.get("mode"),
                    "snapshot_synced_at": prov.get("synced_at"), "collections": len(e.gateway.collections(ctx, s.source_id)),
                    "data_version": v.get("data_version"), "sequence": v.get("sequence"), "sync_status": v.get("sync_status", "no events yet"),
                    "last_sync_at": v.get("last_sync_at"), "last_window_end": v.get("last_window_end"), "last_error": v.get("last_error"),
                    "schema_version": e.kg.source_version(s.source_id, ctx.tenant_id), "knowledge_version": cur["version"] if cur else None})
    return out


@app.get("/runtime/events")
def runtime_events(source_id: Optional[str] = None, limit: int = 50, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    rows = engine().runtime_store.events(ctx.tenant_id, source_id, limit)
    for r in rows:
        p = r.pop("payload", {}) or {}
        r["collections"] = [c["name"] for c in p.get("collections", [])]
    return rows


@app.get("/runtime/investigations")
def runtime_investigations(source_id: Optional[str] = None, limit: int = 30, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().runtime_store.investigations(ctx.tenant_id, source_id, limit)


@app.get("/runtime/answers")
def runtime_answers(limit: int = 30, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().runtime_store.answers(ctx.tenant_id, limit)


@app.get("/runtime/models")
def runtime_models() -> Dict[str, Any]:
    return engine().model_config()


# ============================================================================ files, workspaces, artifacts
@app.post("/files")
def upload_files(files: List[UploadFile] = File(...), ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    out = []
    for f in files:
        try:
            rec = engine().files.save(ctx.tenant_id, ctx.user_id, f.filename or "upload", f.file, content_type=f.content_type or "")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        out.append(rec.public())
    return out


@app.get("/files")
def list_files(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return [r.public() for r in engine().files.list(ctx.tenant_id, ctx.user_id, "upload")]


def _download(ctx: AccessContext, file_id: str, kind: str) -> FileResponse:
    from udre.tools.files import FileAccessDenied

    try:
        rec = engine().files.get(ctx.tenant_id, ctx.user_id, file_id, kind=kind)
    except FileAccessDenied as exc:
        raise HTTPException(404, str(exc))
    return FileResponse(engine().files.path(rec), filename=rec.filename, media_type=rec.content_type or None)


@app.get("/files/{file_id}/download")
def download_file(file_id: str, ctx: AccessContext = Depends(access)) -> FileResponse:
    return _download(ctx, file_id, "upload")


@app.get("/artifacts")
def list_artifacts(run_id: Optional[str] = None, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return [r.public() for r in engine().files.list(ctx.tenant_id, ctx.user_id, "artifact", run_id)]


@app.get("/artifacts/{file_id}/download")
def download_artifact(file_id: str, ctx: AccessContext = Depends(access)) -> FileResponse:
    return _download(ctx, file_id, "artifact")


class WorkspaceRequest(BaseModel):
    file_ids: List[str]


@app.post("/workspaces")
def create_workspace(req: WorkspaceRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from udre.tools.files import FileAccessDenied

    try:
        return engine().workspaces.create(ctx, req.file_ids)
    except (FileAccessDenied, ValueError) as exc:
        raise HTTPException(400, str(exc))


@app.get("/workspaces")
def list_workspaces(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().workspaces.list(ctx)


@app.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        engine().workspaces.close(ctx, workspace_id)
    except AccessDenied as exc:
        raise HTTPException(404, str(exc))
    return {"deleted": workspace_id}


@app.post("/export")
def export(req: ExportRequest) -> Dict[str, Any]:
    e = engine()
    return ToolBox(e.gateway, e.kg, None, e.settings.exports_dir).export_results(req.rows, req.format, req.name)


@app.get("/exports/{filename}")
def download(filename: str) -> FileResponse:
    path = Path(engine().settings.exports_dir) / Path(filename).name
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path)
