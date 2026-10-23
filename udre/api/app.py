"""FastAPI service.

Run: uvicorn udre.api.app:app --port 8000
Identity is taken from ``X-Tenant-Id`` / ``X-User-Id`` headers (replace with real auth in production).
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
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
