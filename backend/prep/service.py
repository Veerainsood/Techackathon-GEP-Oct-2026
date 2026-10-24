"""Data preparation: every uploaded file (and every CRM batch) is checked in the sandbox before anyone uses it.

    profile (sandbox) -> plan (data-prep agent) -> apply safe fixes (sandbox) -> checked copy (new file version)
                                               -> fixes that change or drop values -> Approvals queue
    approval decisions -> apply the approved fixes to the checked copy (sandbox) -> new version -> conversations updated

The original file is never modified; each version records the job and the file it came from. Only this service talks
to the sandbox. Jobs and their plans live in PostgreSQL (``backend_runtime.prep_jobs``).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import Json

from backend.prep.agent import DataPrepAgent, Fix, PrepPlan, classify
from backend.pg_pool import shared_pool
from backend.prep.scripts import APPLY_SCRIPT, PROFILE_SCRIPT
from backend.tools.sandbox import LocalProcessSandbox, PersistentDaytonaSandbox, Sandbox, SandboxError

DDL = """
CREATE SCHEMA IF NOT EXISTS backend_runtime;
CREATE TABLE IF NOT EXISTS backend_runtime.prep_jobs (
    job_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    input_file_id TEXT,
    checked_file_id TEXT,
    filename TEXT NOT NULL,
    profile JSONB,
    plan JSONB,
    fixes JSONB NOT NULL DEFAULT '[]',
    reports JSONB NOT NULL DEFAULT '[]',
    meta JSONB NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS prep_jobs_owner_idx ON backend_runtime.prep_jobs (tenant_id, owner, created_at DESC);
"""
_dumps = lambda o: json.dumps(o, default=str)  # noqa: E731


class PrepError(RuntimeError):
    pass


class PrepJobStore:
    def __init__(self, uri: str):
        self._pool = shared_pool(uri)
        self._exec(DDL)

    def _exec(self, sql: str, params: Any = None, fetch: str = "") -> Any:
        conn = self._pool.getconn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                out = None
                if fetch == "one":
                    out = cur.fetchone()
                elif fetch == "all":
                    out = cur.fetchall()
                if fetch and cur.description:
                    cols = [d[0] for d in cur.description]
                    out = (dict(zip(cols, out)) if out else None) if fetch == "one" else [dict(zip(cols, r)) for r in out]
            conn.commit()
            return out
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def create(self, tenant: str, owner: str, source: str, filename: str, input_file_id: Optional[str]) -> str:
        job_id = f"prep-{uuid.uuid4().hex[:12]}"
        self._exec("""INSERT INTO backend_runtime.prep_jobs (job_id, tenant_id, owner, source, status, input_file_id, filename)
                      VALUES (%s, %s, %s, %s, 'profiling', %s, %s)""", (job_id, tenant, owner, source, input_file_id, filename))
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{k} = %s")
            vals.append(Json(v, dumps=_dumps) if isinstance(v, (dict, list)) else v)
        self._exec(f"UPDATE backend_runtime.prep_jobs SET {', '.join(sets)}, updated_at = now() WHERE job_id = %s", vals + [job_id])

    def get(self, job_id: str, tenant: Optional[str] = None) -> Optional[Dict[str, Any]]:
        row = self._exec("SELECT * FROM backend_runtime.prep_jobs WHERE job_id = %s", (job_id,), fetch="one")
        return row if row and (tenant is None or row["tenant_id"] == tenant) else None

    def list(self, tenant: str, owner: str, limit: int = 30) -> List[Dict[str, Any]]:
        return self._exec("SELECT * FROM backend_runtime.prep_jobs WHERE tenant_id = %s AND owner = %s ORDER BY created_at DESC LIMIT %s",
                          (tenant, owner, limit), fetch="all")


def sandbox_provider() -> str:
    return os.environ.get("BACKEND_PREP_SANDBOX") or ("daytona" if os.environ.get("DAYTONA_API_KEY") else "local")


class PrepService:
    def __init__(self, engine: Any, llm: Any = None, store: Optional[PrepJobStore] = None,
                 sandbox_factory: Optional[Callable[[str], Sandbox]] = None):
        self.e = engine
        self.agent = DataPrepAgent(llm)
        self.store = store
        self.provider = sandbox_provider() if sandbox_factory is None else "custom"
        self._factory = sandbox_factory or self._default_factory
        self._lock = threading.Lock()  # one job at a time in the shared sandbox

    def _default_factory(self, job_id: str) -> Sandbox:
        if self.provider == "daytona":
            return PersistentDaytonaSandbox(job_id, os.environ.get("DAYTONA_SANDBOX", "FinSight"))
        return LocalProcessSandbox(f"prep-{job_id[-8:]}")

    # ------------------------------------------------------------------ sandbox steps
    def _upload(self, box: Sandbox, rel: str, data: bytes, sha: Optional[str] = None) -> str:
        path = box.upload(rel, data)
        if sha and box.sha256(path) != sha:
            raise PrepError(f"integrity check failed for {rel} inside the sandbox")
        return path

    def _run(self, box: Sandbox, script_name: str, script: str, args: List[str], timeout_s: int = 300) -> Dict[str, Any]:
        box.upload(script_name, script.encode())
        res = box.run_script(script_name, args, timeout_s)
        if res.get("exit_code") != 0:
            raise PrepError(f"{script_name} failed in the sandbox: {(res.get('stderr') or res.get('stdout') or '')[-600:]}")
        return res

    def _profile(self, box: Sandbox, data_rel: str) -> Dict[str, Any]:
        self._run(box, "profile.py", PROFILE_SCRIPT, [box.path(data_rel), box.path("out/profile.json")])
        return json.loads(box.download(box.path("out/profile.json")))

    def _apply(self, box: Sandbox, data_rel: str, fixes: List[Dict[str, Any]], out_name: str) -> Dict[str, Any]:
        box.upload("plan.json", json.dumps(fixes).encode())
        self._run(box, "apply.py", APPLY_SCRIPT, [box.path(data_rel), box.path("plan.json"), box.path(f"out/{out_name}"),
                                                   box.path("out/report.json")])
        return {"data": box.download(box.path(f"out/{out_name}")), "report": json.loads(box.download(box.path("out/report.json")))}

    # ------------------------------------------------------------------ public
    def check_file(self, tenant: str, owner: str, file_id: str, on_event: Optional[Callable[[Dict[str, Any]], None]] = None) -> Dict[str, Any]:
        rec = self.e.files.get(tenant, owner, file_id, kind="upload")
        data = self.e.files.read_bytes(rec)
        job_id = self.store.create(tenant, owner, "upload", rec.filename, file_id)
        try:
            out = self._check(job_id, rec.filename, data, rec.sha256, source="upload", on_event=on_event)
            checked = self.e.files.save_bytes(tenant, owner, _checked_name(rec.filename), out["data"], kind="upload",
                                              run_id=job_id, origin=f"prep:{job_id}:from:{file_id}")
            status = "awaiting_approval" if out["pending"] else "done"
            self.store.update(job_id, status=status, checked_file_id=checked.file_id)
            for fix in out["pending"]:
                self._request_approval(tenant, job_id, rec.filename, fix, out["meta"])
            return self.job(job_id)
        except Exception as exc:
            self.store.update(job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            raise

    def _check(self, job_id: str, filename: str, data: bytes, sha: Optional[str], source: str,
               on_event: Optional[Callable[[Dict[str, Any]], None]]) -> Dict[str, Any]:
        with self._lock:
            box = self._factory(job_id)
            try:
                data_rel = f"data/{_safe(filename)}"
                self._upload(box, data_rel, data, sha)
                profile = self._profile(box, data_rel)
                self.store.update(job_id, status="planning", profile=profile)
                plan, meta = self.agent.plan(profile, filename, box.path(data_rel), source,
                                             lambda code: box.run_python(_with_path(code, box.path(data_rel))), on_event)
                fixes = [{"id": f"fix-{i + 1}", **f.model_dump(), "decision": classify(f, profile, source)}
                         for i, f in enumerate(plan.fixes)]
                auto = [f for f in fixes if f["decision"] == "auto"]
                for f in auto:
                    f["status"] = "applied"
                for f in fixes:
                    if f["decision"] == "skipped":
                        f["status"] = "skipped"
                        f["note"] = "not applied to CRM data: the database keeps every record the CRM has"
                pending = [f for f in fixes if f["decision"] == "approval"]
                for f in pending:
                    f["status"] = "pending"
                applied = self._apply(box, data_rel, auto, _safe(filename))
                meta = {**meta, "sandbox": getattr(box, "provider", "custom")}
                self.store.update(job_id, plan=plan.model_dump(), fixes=fixes, reports=[applied["report"]], meta=meta)
                return {"data": applied["data"], "pending": pending, "meta": meta, "plan": plan, "fixes": fixes}
            finally:
                box.close()

    def check_records(self, tenant: str, source: str, object_type: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check a CRM batch before it is loaded. Records that only needed safe fixes are returned cleaned; records touched by
        a fix awaiting approval are held here and released (with the approved fixes) once every fix is decided."""
        label = f"{source} {object_type} batch"
        job_id = self.store.create(tenant, f"service:{source}", source, label, None)
        try:
            data = _records_to_csv(records)
            out = self._check(job_id, f"{source}_{object_type}.csv", data, None, source=source, on_event=None)
            cleaned = _csv_to_records(out["data"])
            held = _held_ids(cleaned, out["pending"])
            released = [r for r in cleaned if r["id"] not in held]
            meta = {**out["meta"], "object_type": object_type, "held_ids": sorted(held),
                    "held_records": [r for r in cleaned if r["id"] in held], "released_records": [], "acknowledged": False}
            self.store.update(job_id, status="awaiting_approval" if out["pending"] else "done", meta=meta)
            for fix in out["pending"]:
                self._request_approval(tenant, job_id, label, fix, out["meta"])
            return {**self.job(job_id), "records": released, "held_ids": sorted(held)}
        except Exception as exc:
            self.store.update(job_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
            raise

    def released_batches(self, tenant: str, source: str) -> List[Dict[str, Any]]:
        rows = self.store._exec("""SELECT job_id FROM backend_runtime.prep_jobs WHERE tenant_id = %s AND source = %s AND status = 'done'
                                   AND meta->>'acknowledged' = 'false' AND jsonb_array_length(meta->'released_records') > 0
                                   ORDER BY created_at""", (tenant, source), fetch="all")
        out = []
        for r in rows:
            j = self.store.get(r["job_id"])
            out.append({"job_id": j["job_id"], "object_type": j["meta"]["object_type"], "records": j["meta"]["released_records"]})
        return out

    def acknowledge(self, tenant: str, job_id: str) -> None:
        j = self.store.get(job_id, tenant)
        if j:
            self.store.update(job_id, meta={**j["meta"], "acknowledged": True})

    def _release_held(self, job: Dict[str, Any], approved: List[Dict[str, Any]]) -> None:
        held = job["meta"].get("held_records") or []
        if held and approved:
            with self._lock:
                box = self._factory(job["job_id"])
                try:
                    data_rel = "data/held.csv"
                    self._upload(box, data_rel, _records_to_csv(held, flat=True))
                    applied = self._apply(box, data_rel, approved, "held.csv")
                finally:
                    box.close()
            released = _csv_to_records(applied["data"])
        else:
            released = held  # every fix rejected: the records are released unchanged
        self.store.update(job["job_id"], status="done", meta={**job["meta"], "released_records": released, "held_records": []})

    def _request_approval(self, tenant: str, job_id: str, filename: str, fix: Dict[str, Any], meta: Dict[str, Any]) -> None:
        cols = ", ".join(fix["columns"]) or "all columns"
        approval_id = f"{job_id}-{fix['id']}"
        now = datetime.now().strftime("%H:%M:%S")
        payload = {
            "id": approval_id, "kind": "prep_fix", "job_id": job_id, "fix_id": fix["id"],
            "agent": "Data prep", "title": f"{_describe(fix)} — {filename}", "entity": f"{filename} · {cols}",
            "amountL": 0, "impact": "Applied to a new version of the file; the original is kept",
            "rationale": fix["rationale"], "status": "pending", "autonomy": "L2", "approval": "Data owner",
            "reversible": True, "sources": [],
            "checks": [{"label": "Operation", "result": "warn", "detail": f"{fix['op']} {json.dumps(fix['params']) if fix['params'] else ''}".strip()},
                       {"label": "Original file kept", "result": "pass", "detail": "changes go to a new version"}],
            "audit": [{"time": now, "actor": f"Data prep agent ({meta.get('model') or meta.get('mode')})",
                       "event": f"Proposed {fix['op']} on {cols}"}],
        }
        self.e.governance.create_approval(tenant, approval_id, payload)

    def decided(self, tenant: str, approval: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Called after an approval decision. When every pending fix of the job is decided, apply the approved ones."""
        payload = approval.get("payload") or {}
        if payload.get("kind") != "prep_fix":
            return None
        job = self.store.get(payload["job_id"], tenant)
        if not job:
            return None
        fixes = job["fixes"]
        for f in fixes:
            if f["id"] == payload["fix_id"]:
                f["status"] = "approved" if approval["status"] == "approved" else "rejected"
        self.store.update(job["job_id"], fixes=fixes)
        if any(f["status"] == "pending" for f in fixes):
            return self.job(job["job_id"])
        approved = [f for f in fixes if f["status"] == "approved"]
        if job["source"] != "upload":
            self._release_held(job, approved)
            for f in approved:
                f["status"] = "applied"
            self.store.update(job["job_id"], fixes=fixes)
            return self.job(job["job_id"])
        if not approved:
            self.store.update(job["job_id"], status="done")
            return self.job(job["job_id"])
        current = self.e.files.get(tenant, job["owner"], job["checked_file_id"], kind="upload")
        with self._lock:
            box = self._factory(job["job_id"])
            try:
                data_rel = f"data/{_safe(current.filename)}"
                self._upload(box, data_rel, self.e.files.read_bytes(current), current.sha256)
                applied = self._apply(box, data_rel, approved, _safe(current.filename))
            finally:
                box.close()
        new = self.e.files.save_bytes(tenant, job["owner"], current.filename, applied["data"], kind="upload", run_id=job["job_id"],
                                      origin=f"prep:{job['job_id']}:from:{current.file_id}")
        for f in approved:
            f["status"] = "applied"
        self.store.update(job["job_id"], status="done", checked_file_id=new.file_id, fixes=fixes,
                          reports=list(job["reports"] or []) + [applied["report"]])
        self._replace_in_conversations(tenant, job["owner"], current.file_id, new.file_id)
        return self.job(job["job_id"])

    def _replace_in_conversations(self, tenant: str, owner: str, old_id: str, new_id: str) -> None:
        """Conversations that use the old version switch to the new one; their workspace is rebuilt on the next question."""
        store = getattr(self.e, "conversations", None)
        if store is None:
            return
        store._exec("""UPDATE backend_runtime.conversations
                       SET file_ids = (SELECT jsonb_agg(CASE WHEN v #>> '{}' = %s THEN to_jsonb(%s::text) ELSE v END)
                                       FROM jsonb_array_elements(file_ids) v),
                           files = (SELECT jsonb_agg(CASE WHEN f->>'file_id' = %s THEN jsonb_set(f, '{file_id}', to_jsonb(%s::text)) ELSE f END)
                                    FROM jsonb_array_elements(files) f),
                           workspace_id = NULL, updated_at = now()
                       WHERE tenant_id = %s AND user_id = %s AND file_ids ? %s""",
                    (old_id, new_id, old_id, new_id, tenant, owner, old_id))

    def job(self, job_id: str) -> Dict[str, Any]:
        j = self.store.get(job_id)
        fixes = j["fixes"] or []
        return {"job_id": j["job_id"], "status": j["status"], "source": j["source"], "filename": j["filename"],
                "input_file_id": j["input_file_id"], "checked_file_id": j["checked_file_id"],
                "summary": (j["plan"] or {}).get("summary"), "findings": (j["plan"] or {}).get("findings", []),
                "fixes": fixes, "applied": sum(1 for f in fixes if f.get("status") == "applied"),
                "pending": sum(1 for f in fixes if f.get("status") == "pending"),
                "rows": (j["profile"] or {}).get("rows"), "error": j["error"],
                "meta": {k: v for k, v in (j["meta"] or {}).items() if not k.endswith("_records")}}


def _records_to_csv(records: List[Dict[str, Any]], flat: bool = False) -> bytes:
    """CRM records ({id, properties}) or flat rows -> CSV with an ``id`` column first."""
    import csv
    import io

    rows = [r if flat else {"id": r["id"], **(r.get("properties") or {})} for r in records]
    cols = ["id"] + sorted({k for r in rows for k in r if k != "id"})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in cols})
    return buf.getvalue().encode()


def _csv_to_records(data: bytes) -> List[Dict[str, Any]]:
    import csv
    import io

    return [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(io.StringIO(data.decode()))]


def _held_ids(rows: List[Dict[str, Any]], pending: List[Dict[str, Any]]) -> set:
    """Ids of the records a pending fix would change or drop."""
    import pandas as pd

    held = set()
    value_cols = lambda r: tuple(v for k, v in sorted(r.items()) if k != "id" and not k.startswith("_duplicate"))  # noqa: E731
    counts: Dict[tuple, int] = {}
    for r in rows:
        counts[value_cols(r)] = counts.get(value_cols(r), 0) + 1
    for fix in pending:
        for r in rows:
            vals = [r.get(c) for c in fix["columns"]]
            if fix["op"] in ("fill_missing", "drop_rows_missing") and any(v in (None, "") for v in vals):
                held.add(r["id"])
            elif fix["op"] == "normalize_values" and any(str(v).strip() in (fix["params"].get("mapping") or {}) for v in vals if v):
                held.add(r["id"])
            elif fix["op"] == "drop_exact_duplicates" and counts[value_cols(r)] > 1:
                held.add(r["id"])
            elif fix["op"] == "cast_numeric" and any(v and pd.isna(pd.to_numeric(str(v).replace(",", ""), errors="coerce")) for v in vals):
                held.add(r["id"])
            elif fix["op"] == "parse_dates" and any(v and pd.isna(pd.to_datetime(v, errors="coerce", format="mixed")) for v in vals):
                held.add(r["id"])
    return held


def _safe(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in Path(name).name)
    return keep or "data.csv"


def _checked_name(filename: str) -> str:
    return filename  # versions are told apart by file id and origin; the user keeps seeing their file's name


def _with_path(code: str, path: str) -> str:
    return f"DATA_PATH = {path!r}\n" + code


def _describe(fix: Dict[str, Any]) -> str:
    cols = ", ".join(fix["columns"])
    return {"fill_missing": f"Fill missing {cols} ({fix['params'].get('method')})",
            "normalize_values": f"Standardize values in {cols}",
            "drop_exact_duplicates": "Remove identical duplicate rows",
            "drop_rows_missing": f"Drop rows missing {cols}",
            "cast_numeric": f"Convert {cols} to numbers",
            "parse_dates": f"Convert {cols} to dates"}.get(fix["op"], f"{fix['op']} {cols}")
