"""FastAPI service.

Run: uvicorn backend.api.app:app --port 8000
Identity is taken from ``X-Tenant-Id`` / ``X-User-Id`` headers (replace with real auth in production).
"""

from __future__ import annotations

import shutil
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.config import ROOT
from backend.engine import Engine, build_engine
from backend.gateway.registry import AccessContext, AccessDenied
from backend.skills.tools import ToolBox

import os

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Backend", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[o for o in os.environ.get("BACKEND_CORS_ORIGINS", "").split(",") if o],
                   allow_origin_regex=os.environ.get("BACKEND_CORS_ORIGIN_REGEX", r"https?://(localhost|127\.0\.0\.1)(:\d+)?"),
                   allow_methods=["*"], allow_headers=["*"])


@lru_cache(maxsize=1)
def engine() -> Engine:
    e = build_engine()
    if os.environ.get("BACKEND_EVENT_WORKER", "1") == "1":
        e.consumer.start(poll_s=float(os.environ.get("BACKEND_EVENT_POLL_S", "1.0")))
    sweep_s = float(os.environ.get("BACKEND_LEAKAGE_SWEEP_S", "900"))
    if sweep_s > 0 and e.leakage:
        e.leakage.start_sweeper(sweep_s)  # always running: detectors also run after procurement sync events
    if os.environ.get("BACKEND_POLICY_STARTUP", "1") == "1" and e.policies:
        threading.Thread(target=_policy_startup, args=(e,), daemon=True, name="policy-startup").start()
    return e


def _policy_startup(e: Engine) -> None:
    """Check every pending invoice and contract against the policies, and learn the current HubSpot stages."""
    for tenant in e.settings.tenants:
        try:
            e.policies.list(tenant)
            e.policies.refresh(tenant)
            e.autopilot.prime(tenant)
        except Exception:
            pass  # retried on the next policy change or event


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
           "total_ms": state["total_ms"], "retried_from": state.get("retried_from"), "detail": state.get("detail"),
           "intent": {k: (state.get("intent") or {}).get(k) for k in ("route", "reason", "mode", "options", "brief", "queried",
                                                                       "standalone_question")},
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
    conversation_id: Optional[str] = None  # continue a conversation; a new one is created when omitted


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


def _conversation(req: AskRequest, ctx: AccessContext) -> Dict[str, Any]:
    """Resolve the conversation for a question: its id, its workspace (recreated from its files after a restart) and history."""
    from backend.runtime.conversations import ConversationNotFound

    e = engine()
    store = e.conversations
    try:
        conv = store.get(ctx.tenant_id, ctx.user_id, req.conversation_id) if req.conversation_id else None
    except ConversationNotFound:
        raise HTTPException(404, f"unknown conversation {req.conversation_id}")
    cid = conv["conversation_id"] if conv else store.create(ctx.tenant_id, ctx.user_id, req.question)
    ws_id = req.workspace_id or (conv or {}).get("workspace_id")
    if ws_id:
        try:
            info = e.workspaces.get(ctx, ws_id)
        except AccessDenied:
            if req.workspace_id or not (conv or {}).get("file_ids"):
                raise HTTPException(404, f"unknown workspace {ws_id}")
            info = e.workspaces.create(ctx, conv["file_ids"])  # workspaces live in memory; files persist
        ws_id = info["workspace_id"]
        if ws_id != (conv or {}).get("workspace_id"):
            store.set_workspace(cid, ws_id, info["files"])
    return {"conversation_id": cid, "workspace_id": ws_id, "history": store.history(cid) if conv else []}


def _record_question(conv: Dict[str, Any], question: str) -> None:
    """Stored when asked, so a conversation reopened mid-answer shows the question as still being answered."""
    engine().conversations.add_message(conv["conversation_id"], "user", question)


def _record_failure(conv: Dict[str, Any], error: str) -> None:
    engine().conversations.add_message(conv["conversation_id"], "assistant", f"The request could not be completed: {error[:300]}",
                                       {"route": "error"})


def _record_turn(conv: Dict[str, Any], question: str, payload: Dict[str, Any]) -> None:
    store = engine().conversations
    detail = payload.get("detail") or {}
    store.add_message(conv["conversation_id"], "assistant", payload["answer"],
                      {k: payload.get(k) for k in ("request_id", "answer_id", "verification_status", "selected_path", "total_ms")}
                      | {"route": (payload.get("intent") or {}).get("route"),
                         "data": (payload.get("data") or [])[:50] if isinstance(payload.get("data"), list) else [],
                         "detail": {k: detail.get(k) for k in ("columns", "rows", "row_count", "limited")} if detail else None})


@app.post("/ask")
def ask(req: AskRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    conv = _conversation(req, ctx)
    _record_question(conv, req.question)
    try:
        state = engine().ask(req.question, ctx=_scoped(req, ctx), mode=req.mode, use_memory=req.use_memory,
                             export_formats=req.export_formats, workspace_id=conv["workspace_id"], history=conv["history"])
    except AccessDenied as exc:
        _record_failure(conv, str(exc))
        raise HTTPException(403, str(exc))
    except Exception as exc:
        _record_failure(conv, f"{type(exc).__name__}: {exc}")
        raise
    payload = result_payload(state, req.question, req.include_candidates) | {k: conv[k] for k in ("conversation_id", "workspace_id")}
    _record_turn(conv, req.question, payload)
    return payload


@app.get("/conversations")
def conversations(limit: int = 30, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().conversations.list(ctx.tenant_id, ctx.user_id, limit)


@app.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.runtime.conversations import ConversationNotFound

    store = engine().conversations
    try:
        conv = store.get(ctx.tenant_id, ctx.user_id, conversation_id)
    except ConversationNotFound:
        raise HTTPException(404, f"unknown conversation {conversation_id}")
    return {"conversation": {k: conv[k] for k in ("conversation_id", "title", "files", "workspace_id", "created_at", "updated_at")},
            "messages": store.messages(conversation_id)}


@app.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.runtime.conversations import ConversationNotFound

    try:
        engine().conversations.delete(ctx.tenant_id, ctx.user_id, conversation_id)
    except ConversationNotFound:
        raise HTTPException(404, f"unknown conversation {conversation_id}")
    return {"deleted": conversation_id}


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
    conv = _conversation(req, ctx)
    _record_question(conv, req.question)
    q.put({"event": "conversation", "conversation_id": conv["conversation_id"], "workspace_id": conv["workspace_id"]})

    def work():
        try:
            state = engine().ask(req.question, ctx=_scoped(req, ctx), mode=req.mode, use_memory=req.use_memory,
                                 export_formats=req.export_formats, workspace_id=conv["workspace_id"], on_event=q.put,
                                 history=conv["history"])
            payload = result_payload(state, req.question) | {k: conv[k] for k in ("conversation_id", "workspace_id")}
            _record_turn(conv, req.question, payload)
            q.put({"event": "result", "result": payload})
        except Exception as exc:
            _record_failure(conv, f"{type(exc).__name__}: {exc}")
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
    from backend.ingestion.base import AuthenticationError
    from backend.ingestion.intune import MicrosoftIntuneSourceAdapter

    try:
        conn = MicrosoftIntuneSourceAdapter(resources=req.resources)
    except AuthenticationError as exc:
        raise HTTPException(400, str(exc))
    return engine().ingestion.ingest(ctx, conn, req.source_id, full=req.full).__dict__


@app.post("/connectors/hubspot")
def connect_hubspot(req: ConnectorRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.ingestion.base import AuthenticationError
    from backend.ingestion.hubspot import HubSpotSourceAdapter

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

    from backend.ingestion.hubspot import verify_webhook_v3

    body = (await request.body()).decode()
    secret = os.environ.get("BACKEND_HUBSPOT_CLIENT_SECRET", "")
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
    """Ingress for the ingestion service. Body: backend.sync-event.v1 JSON; header X-Backend-Signature: HMAC-SHA256 hex."""
    body = await request.body()
    res = engine().consumer.submit(body, request.headers.get("X-Backend-Signature"))
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
        out.append({"source_id": s.source_id, "name": s.name, "description": s.description, "type": s.source_type.value, "mode": prov.get("mode"),
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
    from backend.tools.files import FileAccessDenied

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
    from backend.tools.files import FileAccessDenied

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


# ============================================================================ governance: guardrails and approvals
class GuardrailUpdate(BaseModel):
    mode: str = Field(pattern="^(auto|approve|never)$")


class ApprovalCreate(BaseModel):
    approval_id: str = Field(min_length=1, max_length=100)
    action: Dict[str, Any] = Field(description="The action as the UI shows it; its id must equal approval_id")


class ApprovalDecision(BaseModel):
    approved: bool
    note: Optional[str] = Field(None, max_length=500)


@app.get("/guardrails")
def guardrails(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    """One guardrail per action autopilot can take, shown only where an active policy covers its records (no policy: no
    action, so nothing to decide), with the policies the action is checked against."""
    from backend.policies.actions import ACTIONS
    from backend.policies.engine import SCOPES

    e = engine()
    e.governance.ensure_guardrails(ctx.tenant_id)
    policies = [p for p in (e.policies.list(ctx.tenant_id) if e.policies else []) if p["active"]]
    by_rule = {a["guardrail"]: a for a in ACTIONS.values()}
    out = []
    for g in e.governance.list_guardrails(ctx.tenant_id):
        action = by_rule[g["rule_id"]]
        names = [p["name"] for p in policies if p["collection"] == action["collection"]]
        if names:
            out.append({**g, "action": action["label"], "agent": "Autopilot",
                        "limit": f"{SCOPES[action['collection']]['label']} · Checked against: " + "; ".join(names), "policies": names})
    return out


@app.put("/guardrails/{rule_id}")
def set_guardrail(rule_id: str, body: GuardrailUpdate, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.governance.store import GuardrailLocked
    store = engine().governance
    store.ensure_guardrails(ctx.tenant_id)
    try:
        return store.set_mode(ctx.tenant_id, rule_id, body.mode, ctx.user_id)
    except KeyError:
        raise HTTPException(404, f"unknown guardrail {rule_id}")
    except GuardrailLocked as exc:
        raise HTTPException(403, str(exc))


@app.get("/approvals")
def approvals(status: str = "pending", ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "status must be pending, approved, rejected or all")
    return engine().governance.list_approvals(ctx.tenant_id, status)


@app.post("/approvals", status_code=201)
def create_approval(body: ApprovalCreate, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.governance.store import ApprovalConflict
    if body.action.get("id") != body.approval_id:
        raise HTTPException(400, "action.id must equal approval_id")
    try:
        return engine().governance.create_approval(ctx.tenant_id, body.approval_id, body.action)
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc))


@app.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, body: ApprovalDecision, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.governance.store import ApprovalConflict
    try:
        row = engine().governance.decide(ctx.tenant_id, approval_id, body.approved, ctx.user_id, body.note)
    except KeyError:
        raise HTTPException(404, f"unknown approval {approval_id}")
    except ApprovalConflict as exc:
        raise HTTPException(409, str(exc))
    kind = (row.get("payload") or {}).get("kind")
    try:
        if engine().prep and kind == "prep_fix":
            engine().prep.decided(ctx.tenant_id, row)  # applies the approved fixes once every fix of the job is decided
        elif engine().leakage and kind == "leakage_recovery":
            engine().leakage.decided(ctx.tenant_id, row)  # approved: sent to the mock AP API; rejected: case dismissed
        elif engine().autopilot and kind == "autopilot_action":
            engine().autopilot.decided(ctx.tenant_id, row)  # approved: policies checked again, then recorded as done
    except Exception as exc:
        raise HTTPException(502, f"decision recorded, but carrying it out failed: {type(exc).__name__}: {exc}")
    return row


# ============================================================================ policies and autopilot
@app.get("/policies")
def list_policies(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    """Policies with how many pending records each covers and how many violate it."""
    return engine().policies.summary(ctx.tenant_id)


@app.get("/policies/catalog")
def policy_catalog(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """What a policy can constrain, from the data: collections, fields, their values or ranges, and regions."""
    return engine().policies.catalog(ctx.tenant_id)


def _policy_error(exc: Exception) -> HTTPException:
    from backend.policies.engine import PolicyRejected

    if isinstance(exc, PolicyRejected):
        return HTTPException(409 if exc.conflicts else 400, {"message": str(exc), "conflicts": exc.conflicts})
    if isinstance(exc, KeyError):
        return HTTPException(404, f"unknown policy {exc}")
    raise exc


@app.post("/policies")
def create_policy(body: Dict[str, Any], ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.policies.engine import PolicyIn

    try:
        return engine().policies.save(ctx.tenant_id, PolicyIn(**body), ctx.user_id)
    except Exception as exc:
        raise _policy_error(exc)


@app.put("/policies/{policy_id}")
def update_policy(policy_id: str, body: Dict[str, Any], ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    from backend.policies.engine import PolicyIn

    try:
        return engine().policies.save(ctx.tenant_id, PolicyIn(**body), ctx.user_id, policy_id=policy_id)
    except Exception as exc:
        raise _policy_error(exc)


@app.delete("/policies/{policy_id}")
def delete_policy(policy_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    try:
        engine().policies.delete(ctx.tenant_id, policy_id, ctx.user_id)
    except Exception as exc:
        raise _policy_error(exc)
    return {"deleted": policy_id}


@app.post("/policies/refresh")
def refresh_policies(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """Check every pending invoice and contract against the policies now."""
    return engine().policies.refresh(ctx.tenant_id)


@app.get("/policies/states")
def policy_states(collection: Optional[str] = None, decision: Optional[str] = None, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().policies.states(ctx.tenant_id, collection, decision)


@app.get("/policies/events")
def policy_events(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().policies.events(ctx.tenant_id)


class EvaluateRequest(BaseModel):
    collection: str
    record_id: str
    changes: Dict[str, Any] = Field(default_factory=dict)


@app.post("/policies/evaluate")
def evaluate_policy(body: EvaluateRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """Dry run: what the policies decide for a record, or for the state a change would produce."""
    import re

    from backend.policies.engine import SCOPES

    if body.collection not in SCOPES or not re.fullmatch(r"[A-Za-z0-9_.\-]+", body.record_id):
        raise HTTPException(400, "unknown collection or invalid record id")
    key = "a.agreement_id" if body.collection == "pending_contracts" else "i.invoice_id"
    rows = engine().policies.records(ctx.tenant_id, body.collection, f"{key} = '{body.record_id}'")
    if not rows:
        raise HTTPException(404, f"{body.record_id} is not a pending record")
    return engine().policies.evaluate(ctx.tenant_id, body.collection, rows[0], body.changes)


@app.get("/autopilot/actions")
def autopilot_actions(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().autopilot.actions(ctx.tenant_id)


@app.get("/autopilot/status")
def autopilot_status(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    return {"paused": engine().autopilot.paused(ctx.tenant_id)}


class PauseRequest(BaseModel):
    paused: bool


@app.post("/autopilot/status")
def set_autopilot_status(body: PauseRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """Emergency stop: while paused, autopilot records what it would do but does nothing."""
    return engine().autopilot.set_paused(ctx.tenant_id, body.paused, ctx.user_id)


class SimulateRequest(BaseModel):
    email: str
    lifecyclestage: str = "customer"


@app.post("/autopilot/simulate")
def simulate_hubspot_change(body: SimulateRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """Demo: run autopilot as if HubSpot reported this contact's stage change (nothing changes in HubSpot or the data)."""
    try:
        return engine().autopilot.simulate(ctx.tenant_id, body.email, body.lifecyclestage)
    except KeyError:
        raise HTTPException(404, f"no HubSpot contact {body.email}")


# ============================================================================ spend leakage
@app.post("/leakage/scan")
def leakage_scan(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    return engine().leakage.scan(ctx.tenant_id)


class WorkspaceScanRequest(BaseModel):
    workspace_id: str


@app.post("/leakage/scan-workspace")
def leakage_scan_workspace(body: WorkspaceScanRequest, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    """Find leakage in a workspace's uploaded files: how each file was read, and every case with evidence."""
    try:
        res = engine().leakage.scan_workspace(ctx, body.workspace_id)
    except AccessDenied as exc:
        raise HTTPException(404, str(exc))
    return {k: res[k] for k in ("mapping", "mapping_meta", "report", "cases", "files")}


@app.get("/leakage/summary")
def leakage_summary(ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    return engine().leakage.summary(ctx.tenant_id)


@app.get("/leakage/cases")
def leakage_cases(status: Optional[str] = None, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return engine().leakage.cases(ctx.tenant_id, status)


@app.get("/mock-ap/requests")
def mock_ap_requests(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    """Requests the recovery agent sent to the mock AP / ERP API after approval."""
    return engine().leakage.mock_ap_requests(ctx.tenant_id)


# ============================================================================ data preparation (sandbox)
class PrepFilesRequest(BaseModel):
    file_ids: List[str] = Field(min_length=1, max_length=20)


@app.post("/prep/files")
def prep_files(body: PrepFilesRequest, ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    """Check uploaded files in the sandbox before use: safe fixes are applied to a new version; others go to Approvals."""
    from backend.tools.files import FileAccessDenied

    from concurrent.futures import ThreadPoolExecutor

    def check(fid: str) -> Dict[str, Any]:
        try:
            return engine().prep.check_file(ctx.tenant_id, ctx.user_id, fid)
        except FileAccessDenied:
            raise HTTPException(404, f"unknown file {fid}")
        except Exception as exc:
            raise HTTPException(502, f"checking the file failed: {type(exc).__name__}: {str(exc)[:300]}")

    if len(body.file_ids) <= 1:
        return [check(fid) for fid in body.file_ids]
    with ThreadPoolExecutor(max_workers=min(8, len(body.file_ids))) as pool:  # the service caps sandbox concurrency
        return list(pool.map(check, body.file_ids))


async def _signed_body(request: Request) -> Dict[str, Any]:
    """Service-to-service calls from knowledge-ingestion: HMAC-SHA256 of the raw body with the shared event secret."""
    import json as _json

    from backend.events.contracts import verify_signature

    body = await request.body()
    c = engine().consumer
    if not verify_signature(body, request.headers.get("X-Backend-Signature"), c.secret) and not c.allow_unsigned:
        raise HTTPException(401, "invalid or missing signature")
    try:
        return _json.loads(body)
    except ValueError:
        raise HTTPException(400, "body must be JSON")


@app.post("/prep/batches")
async def prep_batch(request: Request) -> Dict[str, Any]:
    """Check a CRM batch before it is loaded: {tenant_id, source, object_type, records: [{id, properties}]}."""
    from starlette.concurrency import run_in_threadpool

    b = await _signed_body(request)
    try:
        args = (b.get("tenant_id") or "default", b["source"], b["object_type"], b["records"])
    except KeyError as exc:
        raise HTTPException(400, f"missing field {exc}")
    # the data-prep agent runs its own event loop and the sandbox call blocks: never on the server's loop
    return await run_in_threadpool(engine().prep.check_records, *args)


@app.post("/hubspot/files")
async def hubspot_file(request: Request) -> Dict[str, Any]:
    """A file uploaded to HubSpot (signed, from knowledge-ingestion): {tenant_id, file: {id, name, updated_at}, content_b64}.
    Checked by the data-prep agent as CRM data, then read as invoices by the leakage hunter. Each file version runs once."""
    from starlette.concurrency import run_in_threadpool

    b = await _signed_body(request)
    try:
        meta, content = b["file"], b["content_b64"]
    except KeyError as exc:
        raise HTTPException(400, f"missing field {exc}")
    return await run_in_threadpool(_process_hubspot_file, b.get("tenant_id") or "default", meta, content)


def _process_hubspot_file(tenant: str, meta: Dict[str, Any], content_b64: str) -> Dict[str, Any]:
    import base64

    from psycopg2.extras import Json

    import hashlib

    e = engine()
    hid, name, updated = str(meta["id"]), str(meta["name"]), str(meta.get("updated_at") or "")
    data = base64.b64decode(content_b64)
    sha = hashlib.sha256(data).hexdigest()
    seen = e.leakage._exec("""SELECT result FROM backend_runtime.hubspot_files WHERE tenant_id = %s
                              AND ((hubspot_file_id = %s AND updated_at = %s) OR sha256 = %s) LIMIT 1""",
                           (tenant, hid, updated, sha), fetch="one")
    if seen:  # this version, or the same content uploaded again as another file
        return {"duplicate": True, **(seen["result"] or {})}
    owner = "service:hubspot"
    stored = e.files.save_bytes(tenant, owner, name, data, kind="upload", origin=f"hubspot:file:{hid}")
    job = e.prep.check_file(tenant, owner, stored.file_id, source="hubspot")
    scan = e.leakage.scan_file(tenant, owner, job.get("checked_file_id") or stored.file_id, name, source=f"hubspot_file:{hid}")
    result = {"file": name, "hubspot_file_id": hid, "job_id": job["job_id"], "prep": {"applied": job["applied"], "summary": job.get("summary")},
              "leakage": {k: scan[k] for k in ("cases", "new", "recoverable_usd", "by_type", "warnings")}}
    e.leakage._exec("INSERT INTO backend_runtime.hubspot_files (tenant_id, hubspot_file_id, updated_at, name, file_id, job_id, result, sha256) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (tenant, hid, updated, name, stored.file_id, job["job_id"],
                     Json(result, dumps=lambda o: __import__("json").dumps(o, default=str)), sha))
    if e.policies:
        n, usd = scan["cases"], scan["recoverable_usd"]
        e.policies._event(tenant, "hubspot_file", None, hid,
                          f"HubSpot file {name}: {n} leak{'s' if n != 1 else ''} found, ${usd:,.2f} recoverable" if n
                          else f"HubSpot file {name}: no leaks found", result)
    return result


@app.post("/prep/batches/released")
async def prep_released(request: Request) -> List[Dict[str, Any]]:
    """Held records whose fixes have all been decided, ready to load: {tenant_id, source}."""
    from starlette.concurrency import run_in_threadpool

    b = await _signed_body(request)
    return await run_in_threadpool(engine().prep.released_batches, b.get("tenant_id") or "default", b.get("source") or "hubspot")


@app.post("/prep/batches/ack")
async def prep_ack(request: Request) -> Dict[str, Any]:
    """The producer loaded a released batch: {tenant_id, job_id}."""
    from starlette.concurrency import run_in_threadpool

    b = await _signed_body(request)
    await run_in_threadpool(engine().prep.acknowledge, b.get("tenant_id") or "default", b["job_id"])
    return {"acknowledged": b["job_id"]}


@app.get("/prep/jobs")
def prep_jobs(ctx: AccessContext = Depends(access)) -> List[Dict[str, Any]]:
    return [engine().prep.job(j["job_id"]) for j in engine().prep.store.list(ctx.tenant_id, ctx.user_id)]


@app.get("/prep/jobs/{job_id}")
def prep_job(job_id: str, ctx: AccessContext = Depends(access)) -> Dict[str, Any]:
    j = engine().prep.store.get(job_id, ctx.tenant_id)
    if not j or j["owner"] != ctx.user_id:
        raise HTTPException(404, f"unknown job {job_id}")
    return engine().prep.job(job_id)
