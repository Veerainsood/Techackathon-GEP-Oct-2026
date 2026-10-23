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

app = FastAPI(title="Universal Data Reasoning Engine", version="1.0.0")


@lru_cache(maxsize=1)
def engine() -> Engine:
    return build_engine()


def access(x_tenant_id: str = Header("default"), x_user_id: str = Header("anonymous")) -> AccessContext:
    return AccessContext(tenant_id=x_tenant_id, user_id=x_user_id, max_rows=engine().settings.max_rows)


class AskRequest(BaseModel):
    question: str
    mode: str = Field("dual", pattern="^(dual|explorer|hercules)$")
    use_memory: bool = True
    export_formats: List[str] = Field(default_factory=list)
    include_candidates: bool = False


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


@app.post("/ask")
def ask(req: AskRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        state = engine().ask(req.question, ctx=ctx, mode=req.mode, use_memory=req.use_memory, export_formats=req.export_formats)
    except AccessDenied as exc:
        raise HTTPException(403, str(exc))
    d = state["decision"]
    out = {"question": req.question, "answer": d["final_answer"], "verification_status": d["verification_status"],
           "selected_path": d["selected_path"], "agreement_status": d["agreement_status"],
           "confidence_score": d["confidence_score"], "data": d["final_structured_data"], "query": d["winning_query"],
           "source_id": d["winning_source_id"], "query_lineage": d["query_lineage"],
           "disagreement_analysis": d.get("disagreement_analysis"), "assumptions": d["unresolved_assumptions"],
           "evidence": [{"id": e["evidence_id"], "type": e["evidence_type"], "action": e["query_or_action"],
                         "observed": e["observed_summary"]} for e in d["supporting_evidence"]],
           "validation": [{"validator": f["validator_name"], "passed": f["passed"], "severity": f["severity"],
                           "message": f["message"]} for f in d["validation_findings"]],
           "exports": state.get("exports", []), "trace": state["trace"], "total_ms": state["total_ms"]}
    if req.include_candidates:
        out["candidates"] = {k: state.get(k) for k in ("explorer_candidate", "hercules_candidate")}
    return out


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
            state = engine().workflow.run(req.question, ctx, mode=req.mode, use_memory=req.use_memory,
                                          export_formats=req.export_formats, on_event=q.put)
            q.put({"event": "decision", "decision": {k: state["decision"][k] for k in (
                "final_answer", "verification_status", "selected_path", "agreement_status", "winning_query", "query_lineage")}})
        except Exception as exc:
            q.put({"event": "error", "error": str(exc)})
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
