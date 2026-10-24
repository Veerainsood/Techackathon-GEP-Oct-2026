"""Leakage hunting on uploaded files, end to end through the API exactly as the app does it: upload the 14-file audit pack,
check every file in the sandbox (in parallel), create the workspace, ask "find the leaks" and score the answer's rows against
the answer key (>= 80% detected with the right value, <= 10% false positives, no legitimate look-alike flagged).
Also: the manual-approval test file produces approvals, and deciding them in the API completes the job."""

import io
import json

import pytest

TENANT, USER = "t_upload_leaks", "analyst"
H = {"X-Tenant-Id": TENANT, "X-User-Id": USER}


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    from backend.synthetic.audit_pack import write_approval_file, write_pack

    out = tmp_path_factory.mktemp("test_files")
    key = write_pack(out)
    return {"pack": sorted((out / "leakage_audit_pack").iterdir()), "key": key, "approval": write_approval_file(out)}


@pytest.fixture(scope="module")
def client(pg_uri, uploads_dir):
    from fastapi.testclient import TestClient

    from backend.api import app as api
    from backend.engine import build_engine

    e = build_engine(llm_providers=(None,) * 5)
    original = api.engine
    api.engine = lambda: e
    yield TestClient(api.app)
    api.engine = original


def test_header_mapping_reads_every_file_of_the_pack(files):
    import duckdb
    import pandas as pd

    from backend.gateway.file_adapter import _safe_name
    from backend.leakage.ledger import heuristic_mapping

    con = duckdb.connect()
    tables = {_safe_name(f.stem): pd.read_excel(f) if f.suffix == ".xlsx" else con.execute(f"SELECT * FROM read_csv_auto('{f.as_posix()}')").df()
              for f in files["pack"]}
    roles = {t.table: t.role for t in heuristic_mapping(tables).tables}
    assert roles == {"ap_invoices_erp_jan_jun": "invoices", "ap_invoices_erp_jul_dec": "invoices", "scanned_invoices_ocr": "invoices",
                     "bank_payments_2025": "payments", "contract_price_list": "price_list", "credit_notes": "credit_notes",
                     "fx_rates_2025": "fx_rates", "gl_journal_extract": "ignore", "invoice_lines": "invoice_lines",
                     "purchase_orders": "purchase_orders", "saas_licences": "saas_licences", "saas_user_activity": "saas_activity",
                     "supplier_contracts": "contracts", "vendor_master": "vendors"}


def test_upload_check_and_ask_find_the_leaks(client, files):
    from backend.leakage.evaluation import score
    from backend.leakage.service import TYPE_LABEL

    up = client.post("/files", headers=H, files=[("files", (f.name, io.BytesIO(f.read_bytes()), "application/octet-stream"))
                                                 for f in files["pack"]])
    assert up.status_code == 200, up.text
    jobs = client.post("/prep/files", headers=H, json={"file_ids": [x["file_id"] for x in up.json()]}).json()
    assert len(jobs) == 14 and all(j["status"] in ("done", "awaiting_approval") for j in jobs)
    ws = client.post("/workspaces", headers=H, json={"file_ids": [j["checked_file_id"] or j["input_file_id"] for j in jobs]}).json()
    r = client.post("/ask", headers=H, json={"question": "Find the leaks in these files", "workspace_id": ws["workspace_id"]})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["intent"]["route"] == "leakage" and payload["verification_status"] == "verified_fact"
    label = {v: k for k, v in TYPE_LABEL.items()}
    cases = [{"type": label[x["leak_type"]], "ref": x["record"], "recoverable_usd": x["recoverable_usd"]} for x in payload["data"]]
    s = score(cases, files["key"])
    assert s["detection_rate"] >= 0.8, (s["missed"], s["wrong_value"])
    assert s["false_positive_rate"] <= 0.10 and s["exceptions_flagged"] == [], (s["false_positives"], s["exceptions_flagged"])
    assert "likely leaks worth" in payload["answer"] and "gl_journal_extract" in payload["answer"]  # says what it did not use


def test_approval_file_needs_a_person_and_decisions_complete_the_job(client, files):
    csv = next(p for p in files["approval"] if p.suffix == ".csv")
    fid = client.post("/files", headers=H, files=[("files", (csv.name, io.BytesIO(csv.read_bytes()), "text/csv"))]).json()[0]["file_id"]
    job = client.post("/prep/files", headers=H, json={"file_ids": [fid]}).json()[0]
    assert job["pending"] >= 1 and job["applied"] >= 1  # stray spaces fixed on their own; ambiguous dates wait for a person
    pending = [a for a in client.get("/approvals?status=pending", headers=H).json() if a["payload"].get("job_id") == job["job_id"]]
    assert pending and all(a["payload"]["kind"] == "prep_fix" for a in pending)
    for a in pending:
        assert client.post(f"/approvals/{a['approval_id']}/decision", headers=H, json={"approved": True}).status_code == 200
    done = client.get(f"/prep/jobs/{job['job_id']}", headers=H).json()
    assert done["status"] == "done" and done["pending"] == 0 and done["checked_file_id"] != job["checked_file_id"]
    assert json.dumps(done)  # serialisable for the chat card
