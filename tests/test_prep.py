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


def test_safe_fixes_apply_automatically_and_duplicates_are_flagged_not_removed(engine):
    job = engine.prep.check_file(TENANT, USER, upload(engine).file_id)
    assert job["status"] == "awaiting_approval" and job["meta"]["sandbox"] == "local"
    ops = {f["op"]: f for f in job["fixes"]}
    assert ops["trim_whitespace"]["status"] == "applied" and ops["flag_duplicates"]["status"] == "applied"
    assert ops["fill_missing"]["decision"] == "approval" and ops["fill_missing"]["status"] == "pending"
    data = rows(engine, job["checked_file_id"])
    assert len(data) == 6  # nothing removed
    assert data[0]["vendor"] == "Acme"  # trimmed
    dup = [r for r in data if r["subscription_id"] == "S4"]
    assert all(r["_duplicate_group"] and r["_duplicate_count"] == "2" for r in dup)
    assert any("Identical rows" == f["title"] for f in job["findings"])
    approvals = [a for a in engine.governance.list_approvals(TENANT, "pending") if a["payload"].get("job_id") == job["job_id"]]
    assert approvals and all(a["payload"]["kind"] == "prep_fix" and a["payload"]["agent"] == "Data prep" for a in approvals)
    original = engine.files.get(TENANT, USER, job["input_file_id"], kind="upload")
    assert engine.files.read_bytes(original) == MESSY  # the original is never modified


def test_approved_fixes_create_a_new_version_and_conversations_switch_to_it(engine):
    job = engine.prep.check_file(TENANT, USER, upload(engine).file_id)
    cid = engine.conversations.create(TENANT, USER, "subscriptions")
    engine.conversations.set_workspace(cid, "ws-old", [{"file_id": job["checked_file_id"], "filename": "subscriptions.csv"}])
    for a in [a for a in engine.governance.list_approvals(TENANT, "pending") if a["payload"].get("job_id") == job["job_id"]]:
        approve = a["payload"]["title"].startswith("Fill missing monthly_usd")
        row = engine.governance.decide(TENANT, a["approval_id"], approve, "approver", None)
        result = engine.prep.decided(TENANT, row)
    assert result["status"] == "done" and result["checked_file_id"] != job["checked_file_id"]
    data = rows(engine, result["checked_file_id"])
    assert [r["monthly_usd"] for r in data if r["subscription_id"] in ("S2", "S5")] == ["1200.0", "1200.0"]  # median of the numbers
    conv = engine.conversations.get(TENANT, USER, cid)
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


def test_day_month_ambiguous_dates_need_approval(engine):
    data = b"id,start_date\n1,2026-01-05\n2,05/02/2026\n3,2026-03-01\n"
    job = engine.prep.check_file(TENANT, USER, upload(engine, data, "dates.csv").file_id)
    parse = next(f for f in job["fixes"] if f["op"] == "parse_dates")
    assert parse["decision"] == "approval"  # 5 Feb or 2 May: a person chooses


def test_investigating_agents_have_no_python_tool(engine):
    from backend.agents.llm_agent import available_tools
    from backend.gateway.registry import AccessContext
    from backend.skills.tools import ToolSession

    session = ToolSession(engine.toolbox(use_memory=False), AccessContext(tenant_id="default"), "Explorer")
    assert "run_python" not in available_tools(session)


def test_api_checks_uploads_and_applies_decisions(engine):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    original = api.engine
    api.engine = lambda: engine
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": TENANT, "X-User-Id": USER}
        fid = c.post("/files", files=[("files", ("subs.csv", io.BytesIO(MESSY), "text/csv"))], headers=h).json()[0]["file_id"]
        job = c.post("/prep/files", json={"file_ids": [fid]}, headers=h).json()[0]
        assert job["applied"] >= 2 and job["pending"] >= 1
        pending = [a for a in c.get("/approvals?status=pending", headers=h).json() if a["payload"].get("job_id") == job["job_id"]]
        for a in pending:
            assert c.post(f"/approvals/{a['approval_id']}/decision", json={"approved": False}, headers=h).status_code == 200
        done = c.get(f"/prep/jobs/{job['job_id']}", headers=h).json()
        assert done["status"] == "done" and done["pending"] == 0
        assert c.get(f"/prep/jobs/{job['job_id']}", headers={"X-Tenant-Id": TENANT, "X-User-Id": "other"}).status_code == 404
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
