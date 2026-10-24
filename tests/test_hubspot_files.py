"""Files uploaded to HubSpot: knowledge-ingestion polls them and the backend checks each one with the data-prep agent (as CRM
data: no approvals) and reads it as invoices for the leak check. Scored against the file's answer key."""

import dataclasses
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
T = "t_hubspot_files"


@pytest.fixture(scope="module")
def setup(pg_uri, uploads_dir, tmp_path_factory):
    from fastapi.testclient import TestClient

    from backend.api import app as api
    from backend.config import Settings
    from backend.engine import build_engine
    from backend.synthetic.audit_pack import write_hubspot_file

    out = tmp_path_factory.mktemp("hs")
    key = write_hubspot_file(out)
    e = build_engine(dataclasses.replace(Settings(), tenants=[T]), llm_providers=(None,) * 5)
    e.consumer.secret = "s"
    for table in ("leakage_cases", "hubspot_files", "approvals", "policy_events"):
        e.leakage._exec(f"DELETE FROM backend_runtime.{table} WHERE tenant_id = %s", (T,))
    original = api.engine
    api.engine = lambda: e
    yield {"e": e, "client": TestClient(api.app), "key": key, "csv": out / key["file"]}
    api.engine = original


def post(client, payload):
    from backend.events.contracts import sign

    body = json.dumps(payload).encode()
    r = client.post("/hubspot/files", content=body, headers={"X-Backend-Signature": sign(body, "s"), "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_hubspot_file_is_checked_and_its_leaks_found(setup):
    import base64

    from backend.leakage.evaluation import score

    e, csv = setup["e"], setup["csv"]
    payload = {"tenant_id": T, "file": {"id": "hs-file-1", "name": csv.name, "updated_at": "2026-10-24T08:00:00Z"},
               "content_b64": base64.b64encode(csv.read_bytes()).decode()}
    res = post(setup["client"], payload)
    assert res["leakage"]["cases"] >= 1 and res["prep"]["applied"] >= 0
    cases = [{"type": c["leakage_type"], "ref": c["record_id"], "recoverable_usd": c["recoverable_usd"]} for c in e.leakage.cases(T)]
    s = score(cases, setup["key"])
    assert s["detection_rate"] >= 0.8 and not s["wrong_value"], (s["missed"], s["wrong_value"])
    assert s["false_positive_rate"] <= 0.1 and s["exceptions_flagged"] == [], s["false_positives"]
    assert all(c["source"] == "hubspot_file:hs-file-1" and c["source_label"] == csv.name for c in e.leakage.cases(T))
    jobs = [j for j in e.prep.store.list(T, "service:hubspot")]
    assert jobs and all(e.prep.job(j["job_id"])["pending"] == 0 for j in jobs)  # CRM data: no approvals
    drafts = [a for a in e.governance.list_approvals(T, "pending") if a["payload"]["kind"] == "leakage_recovery"]
    assert len(drafts) == len(cases)
    assert any(ev["kind"] == "hubspot_file" for ev in e.policies.events(T))
    # the same file version again: nothing new
    assert post(setup["client"], payload)["duplicate"] is True and len(e.leakage.cases(T)) == len(cases)


def test_the_warehouse_sweep_does_not_resolve_cases_from_files(setup):
    e = setup["e"]
    before = {c["case_id"]: c["status"] for c in e.leakage.cases(T) if c["source"].startswith("hubspot_file")}
    e.leakage.scan(T)
    after = {c["case_id"]: c["status"] for c in e.leakage.cases(T) if c["source"].startswith("hubspot_file")}
    assert before and after == before


def test_ingestion_polls_hubspot_files_and_hands_them_to_the_backend(setup, tmp_path):
    with mock.patch.dict(os.environ, {"HUBSPOT_PORTAL_ID": "test", "BACKEND_EVENTS_URL": "http://in-process"}):
        sys.path.insert(0, str(ROOT / "knowledge-ingestion"))
        from knowledge_ingestion.config import BackendConfig, config
        from knowledge_ingestion.connectors.hubspot.client import HubSpotFile, HubSpotScopeMissing
        from knowledge_ingestion.pipeline import hubspot_files
        from knowledge_ingestion.publish import backend_files
        from knowledge_ingestion.storage import sql_store

        store = sql_store.SqlStore(f"sqlite:///{tmp_path / 'files.db'}")
        sql_store.metadata.create_all(store._engine)
        csv = setup["csv"]
        f = HubSpotFile("hs-file-2", csv.name, "csv", "2026-10-24T09:00:00Z", csv.stat().st_size)

        class Fake:
            calls = 0

            def files_updated_since(self, since_ms):
                return [f]

            def download_file(self, file_id):
                Fake.calls += 1
                return csv.read_bytes()

        sent = []

        def submit(file, content):
            import base64
            res = post(setup["client"], {"tenant_id": T, "file": {"id": file.id, "name": file.name, "updated_at": file.updated_at},
                                         "content_b64": base64.b64encode(content).decode()})
            sent.append(res)
            return res

        cfg = replace(config, backend=BackendConfig(events_url="http://in-process", secret="s", tenant_id=T, source_id="hubspot", database_id=None))
        with mock.patch.object(hubspot_files, "config", cfg), mock.patch.object(backend_files, "submit", submit):
            n = len(setup["e"].leakage.cases(T))
            out = hubspot_files.sync_files(store, client=Fake())
            # the same content as the file in the first test, uploaded again as another HubSpot file: not counted twice
            assert out["errors"] == [] and out["files"][0]["name"] == csv.name and sent[0]["duplicate"] is True
            assert len(setup["e"].leakage.cases(T)) == n
            assert store.get_cursor("hubspot_files", "files") is not None
            assert hubspot_files.sync_files(store, client=Fake())["files"][0]["duplicate"] is True  # same version: skipped by the backend

            class NoScope(Fake):
                def files_updated_since(self, since_ms):
                    raise HubSpotScopeMissing("no files scope")

            assert "files" in hubspot_files.sync_files(store, client=NoScope())["skipped"]  # contacts keep syncing elsewhere
