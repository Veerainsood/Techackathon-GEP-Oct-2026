"""Data preparation in the sandbox (local runner in tests; the live Daytona sandbox is exercised by an opt-in smoke test).

Uploaded files are profiled and fixed in the sandbox: safe fixes apply automatically, duplicates are flagged and never
removed without approval, value-changing fixes go to the Approvals queue, and approved fixes produce a new file version
that conversations switch to. Only the data-prep agent has a Python tool."""

import csv
import io
import json
import os

import pytest

from backend.llm.scripted import ScriptedProvider, last_tool_result, tool

TENANT, USER = "t_prep", "prep-user"
MESSY = (b"subscription_id,vendor,product,monthly_usd,start_date,region\n"
         b"S1, Acme ,CRM Pro,1200,2026-01-05,AMER\n"
         b"S2,Globex,Analytics,n/a,2026-02-10,emea\n"
         b"S3,Initech,CRM Pro,900,,AMER\n"
         b"S4,Acme,CRM Pro,1200,2026-01-05,AMER\n"
         b"S4,Acme,CRM Pro,1200,2026-01-05,AMER\n"
         b"S5,Umbrella,Security,,2026-03-01,APAC\n")


@pytest.fixture(scope="module")
def engine(pg_uri, uploads_dir):
    from backend.engine import build_engine

    return build_engine(llm_providers=(None, None, None, None, None))


def upload(e, data=MESSY, name="subscriptions.csv"):
    return e.files.save_bytes(TENANT, USER, name, data, kind="upload")


def rows(e, file_id):
    rec = e.files.get(TENANT, USER, file_id, kind="upload")
    return list(csv.DictReader(io.StringIO(e.files.read_bytes(rec).decode())))


def test_safe_fixes_and_imputation_apply_automatically_and_duplicates_are_flagged_not_removed(engine):
    job = engine.prep.check_file(TENANT, USER, upload(engine).file_id)
    assert job["status"] == "done" and job["meta"]["sandbox"] == "local" and job["pending"] == 0
    ops = {f["op"]: f for f in job["fixes"]}
    assert ops["trim_whitespace"]["status"] == "applied" and ops["flag_duplicates"]["status"] == "applied"
    data = rows(engine, job["checked_file_id"])
    assert len(data) == 6  # nothing removed
    assert data[0]["vendor"] == "Acme"  # trimmed
    dup = [r for r in data if r["subscription_id"] == "S4"]
    assert all(r["_duplicate_group"] and r["_duplicate_count"] == "2" for r in dup)
    assert any("Identical rows" == f["title"] for f in job["findings"])
    assert not [a for a in engine.governance.list_approvals(TENANT, "pending") if a["payload"].get("job_id") == job["job_id"]]
    original = engine.files.get(TENANT, USER, job["input_file_id"], kind="upload")
    assert engine.files.read_bytes(original) == MESSY  # the original is never modified

    filled = engine.prep.check_file(TENANT, USER, upload(engine, b"id,qty,owner\n1,5,Ann\n2,,\n3,7,Bo\n4,9,Cy\n", "qty.csv").file_id)
    fill = next(f for f in filled["fixes"] if f["op"] == "fill_missing")
    assert fill["decision"] == "auto" and fill["status"] == "applied" and fill["columns"] == ["qty"]
    assert [(r["qty"], r["owner"]) for r in rows(engine, filled["checked_file_id"]) if r["id"] == "2"] == [("7.0", "")]  # no invented name


def dropper(pg_uri, uploads_dir):
    """An engine whose agent proposes removing rows that have no id (the only kind of fix that waits for a person)."""
    from backend.engine import build_engine

    def policy(system, messages, tools):
        return tool("submit_plan", summary="checked", fixes=[
            {"op": "trim_whitespace", "columns": ["vendor"], "rationale": "stray spaces"},
            {"op": "drop_rows_missing", "columns": ["id"], "rationale": "rows without an id cannot be used"}])

    return build_engine(llm_providers=(None, None, None, None, ScriptedProvider(policy)))


def test_approved_fixes_create_a_new_version_and_conversations_switch_to_it(pg_uri, uploads_dir):
    e = dropper(pg_uri, uploads_dir)
    job = e.prep.check_file(TENANT, USER, upload(e, b"id,vendor\n1, Acme\n,Globex\n3,Initech\n", "ids.csv").file_id)
    assert job["pending"] == 1 and job["applied"] == 1
    cid = e.conversations.create(TENANT, USER, "ids")
    e.conversations.set_workspace(cid, "ws-old", [{"file_id": job["checked_file_id"], "filename": "ids.csv"}])
    for a in [a for a in e.governance.list_approvals(TENANT, "pending") if a["payload"].get("job_id") == job["job_id"]]:
        result = e.prep.decided(TENANT, e.governance.decide(TENANT, a["approval_id"], True, "approver", None))
    assert result["status"] == "done" and result["checked_file_id"] != job["checked_file_id"]
    assert [r["id"] for r in rows(e, result["checked_file_id"])] == ["1", "3"]
    conv = e.conversations.get(TENANT, USER, cid)
    assert conv["file_ids"] == [result["checked_file_id"]] and conv["workspace_id"] is None  # rebuilt on the next question


def test_llm_plan_investigates_in_the_sandbox_and_bad_plans_are_sent_back(pg_uri, uploads_dir):
    from backend.engine import build_engine

    calls = {"n": 0}

    def policy(system, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool("run_python", gap="are the S4 rows a repeated subscription?",
                        code="import pandas as pd; d = pd.read_csv(DATA_PATH); print(d[d.subscription_id == 'S4'].shape[0])")
        if calls["n"] == 2:
            assert "2" in last_tool_result(messages)  # real output from the sandbox
            return tool("submit_plan", summary="x", fixes=[{"op": "trim_whitespace", "columns": ["not_a_column"], "rationale": "x"}])
        return tool("submit_plan", summary="Subscriptions checked",
                    findings=[{"title": "Possible duplicate subscription", "severity": "critical",
                               "detail": "Acme CRM Pro (S4) is billed twice: $1,200/month of possible overspend"}],
                    fixes=[{"op": "flag_duplicates", "columns": ["subscription_id"], "rationale": "make the repeat visible"},
                           {"op": "drop_exact_duplicates", "rationale": "only if the second row is a load error"}])

    e = build_engine(llm_providers=(None, None, None, None, ScriptedProvider(policy)))
    job = e.prep.check_file(TENANT, USER, upload(e).file_id)
    assert calls["n"] == 3 and job["meta"]["mode"] == "llm" and job["meta"]["investigations"] == 1
    ops = {f["op"]: f for f in job["fixes"]}
    assert ops["flag_duplicates"]["status"] == "applied" and ops["drop_exact_duplicates"]["decision"] == "approval"
    assert job["findings"][0]["severity"] == "critical" and len(rows(e, job["checked_file_id"])) == 6


def test_computed_fill_uses_product_and_formulas_as_text_are_sent_back(pg_uri, uploads_dir):
    from backend.engine import build_engine

    calls = {"n": 0}

    def policy(system, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:  # a formula written as a constant would put the text into every gap
            return tool("submit_plan", summary="x", fixes=[{"op": "fill_missing", "columns": ["total"], "rationale": "x",
                                                            "params": {"method": "constant", "value": "qty * price"}}])
        assert "names a column" in last_tool_result(messages)
        return tool("submit_plan", summary="Lines checked", fixes=[{"op": "fill_missing", "columns": ["total"], "rationale": "qty x price",
                                                                    "params": {"method": "product", "of": ["qty", "price"]}}])

    e = build_engine(llm_providers=(None, None, None, None, ScriptedProvider(policy)))
    job = e.prep.check_file(TENANT, USER, upload(e, b"id,qty,price,total\n1,2,5,10\n2,3,4.5,\n", "lines.csv").file_id)
    assert job["fixes"][0]["status"] == "applied" and job["pending"] == 0
    assert [float(r["total"]) for r in rows(e, job["checked_file_id"])] == [10.0, 13.5]


def test_data_fixes_never_wait_for_a_person_even_for_uploads(engine):
    data = b"id,start_date,amount\n1,2026-01-05,10\n2,05/02/2026,TBC\n3,2026-03-01,30\n"
    job = engine.prep.check_file(TENANT, USER, upload(engine, data, "dates.csv").file_id)
    assert job["pending"] == 0 and all(f["decision"] == "auto" for f in job["fixes"])
    assert any(f["op"] == "parse_dates" and f["status"] == "applied" for f in job["fixes"])


def test_investigating_agents_have_no_python_tool(engine):
    from backend.agents.llm_agent import available_tools
    from backend.gateway.registry import AccessContext
    from backend.skills.tools import ToolSession

    session = ToolSession(engine.toolbox(use_memory=False), AccessContext(tenant_id="default"), "Explorer")
    assert "run_python" not in available_tools(session)


def test_api_checks_uploads_and_applies_decisions(pg_uri, uploads_dir):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    engine = dropper(pg_uri, uploads_dir)
    original = api.engine
    api.engine = lambda: engine
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": TENANT, "X-User-Id": USER}
        ids = b"id,vendor\n1, Acme\n,Globex\n3,Initech\n"
        fid = c.post("/files", files=[("files", ("ids.csv", io.BytesIO(ids), "text/csv"))], headers=h).json()[0]["file_id"]
        job = c.post("/prep/files", json={"file_ids": [fid]}, headers=h).json()[0]
        assert job["applied"] >= 1 and job["pending"] == 1  # trimming applied; removing the row without an id waits
        pending = [a for a in c.get("/approvals?status=pending", headers=h).json() if a["payload"].get("job_id") == job["job_id"]]
        for a in pending:
            assert c.post(f"/approvals/{a['approval_id']}/decision", json={"approved": False}, headers=h).status_code == 200
        done = c.get(f"/prep/jobs/{job['job_id']}", headers=h).json()
        assert done["status"] == "done" and done["pending"] == 0
        assert c.get(f"/prep/jobs/{job['job_id']}", headers={"X-Tenant-Id": TENANT, "X-User-Id": "other"}).status_code == 404
    finally:
        api.engine = original


def test_hubspot_batches_are_planned_by_the_agent_through_the_api(pg_uri, uploads_dir):
    """The batch endpoint is async; the agent runs its own event loop, so it must not run on the server's loop."""
    from fastapi.testclient import TestClient

    from backend.api import app as api
    from backend.engine import build_engine
    from backend.events.contracts import sign

    def policy(system, messages, tools):
        return tool("submit_plan", summary="HubSpot contacts checked",
                    fixes=[{"op": "trim_whitespace", "columns": ["company"], "rationale": "stray spaces"},
                           {"op": "fill_missing", "columns": ["lifecyclestage"], "params": {"method": "constant", "value": "lead"},
                            "rationale": "HubSpot's default stage for new contacts"},
                           {"op": "drop_rows_missing", "columns": ["email"], "rationale": "no email"}])

    e = build_engine(llm_providers=(None, None, None, None, ScriptedProvider(policy)))
    e.consumer.secret = "test-secret"
    original = api.engine
    api.engine = lambda: e
    try:
        body = json.dumps({"tenant_id": TENANT, "source": "hubspot", "object_type": "contacts",
                           "records": [{"id": "101", "properties": {"email": "a@x.com", "company": " Contoso Logistics ", "lifecyclestage": "customer"}},
                                       {"id": "102", "properties": {"email": None, "company": "Woodgrove Bank", "lifecyclestage": None}}]}).encode()
        r = TestClient(api.app).post("/prep/batches", content=body,
                                     headers={"X-Backend-Signature": sign(body, "test-secret"), "Content-Type": "application/json"})
        assert r.status_code == 200, r.text
        job = r.json()
        assert job["meta"]["mode"] == "llm", job["meta"].get("failure")
        assert job["pending"] == 0 and job["held_ids"] == []  # CRM data: applied without approval
        assert [(x["company"], x["lifecyclestage"]) for x in job["records"]] == [("Contoso Logistics", "customer"), ("Woodgrove Bank", "lead")]
        status = {f["op"]: f["status"] for f in job["fixes"]}
        assert status == {"trim_whitespace": "applied", "fill_missing": "applied", "drop_rows_missing": "skipped"}  # no record dropped
    finally:
        api.engine = original


@pytest.mark.skipif(os.environ.get("BACKEND_LIVE_SMOKE") != "1", reason="live Daytona smoke test (BACKEND_LIVE_SMOKE=1)")
def test_live_daytona_sandbox_profiles_and_fixes(engine, monkeypatch):
    from backend.llm.config import _load_dotenv
    from backend.prep.service import PrepService

    _load_dotenv()
    monkeypatch.setenv("BACKEND_PREP_SANDBOX", "daytona")
    svc = PrepService(engine, None, engine.prep.store)
    job = svc.check_file(TENANT, USER, upload(engine).file_id)
    assert job["meta"]["sandbox"] == "daytona" and rows(engine, job["checked_file_id"])[0]["vendor"] == "Acme"
