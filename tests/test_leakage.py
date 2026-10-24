"""Spend-leakage hunting against the seeded answer key (isolated copy of the warehouse with procurement data).

Acceptance criteria from the brief: at least 80% of seeded cases detected with the correct recoverable value, at most
10% false positives with legitimate exceptions not flagged, every case with evidence, root cause and confidence,
prioritised by value, and recovery executed only after approval through the mock AP API."""

import pytest

TENANT = "t_leak"


@pytest.fixture(scope="module")
def hunt(generator):
    from backend import pg_embedded
    from backend.config import Settings, pg_uri
    from backend.engine import build_engine
    from backend.gateway.postgres_adapter import PostgresSourceAdapter
    from backend.leakage.service import LeakageService
    from backend.synthetic.loader import load_to_postgres
    from backend.synthetic.procurement import ProcurementGenerator, load

    lab = pg_embedded.ensure_database("backend_leakage_lab")
    load_to_postgres(generator.frames, lab)
    groups = {r["conglomerate_name"]: r["conglomerate_id"] for r in generator.frames["master_ref.client_conglomerates"].to_dict("records")}
    data = ProcurementGenerator(groups=groups).generate()
    load(data, lab)
    e = build_engine(llm_providers=(None,) * 5)
    e.gateway.register(PostgresSourceAdapter("leak_lab", lab, include_schemas=["procurement"]), tenants=[TENANT])
    svc = LeakageService(e, pg_uri(Settings().knowledge_db), source_id="leak_lab")
    def clean() -> None:  # the cases table is shared with the live tenant and readable by the agents
        svc._exec("DELETE FROM backend_runtime.leakage_cases WHERE tenant_id = %s", (TENANT,))
        svc._exec("DELETE FROM backend_runtime.mock_ap_requests WHERE tenant_id = %s", (TENANT,))
        e.governance._exec("DELETE FROM backend_runtime.approvals WHERE tenant_id = %s", (TENANT,))

    clean()
    e.leakage = svc
    yield {"e": e, "svc": svc, "key": {"cases": data.answer_key, "legitimate_exceptions": data.exceptions}}
    clean()


def test_meets_the_acceptance_criteria_against_the_answer_key(hunt):
    from backend.leakage.evaluation import score

    s = score(hunt["svc"].detect(TENANT), hunt["key"])
    assert s["detection_rate"] >= 0.8 and not s["wrong_value"]
    assert s["false_positive_rate"] <= 0.10 and s["exceptions_flagged"] == []
    assert set(s["by_type"]) == {"duplicate_invoice", "price_variance", "missed_discount", "overpayment", "unused_saas"}


def test_every_case_has_evidence_root_cause_confidence_and_cases_are_prioritised(hunt):
    cases = hunt["svc"].detect(TENANT)
    assert all(c["evidence"] and c["evidence"][0]["rows"] and c["root_cause"] and 0 < c["confidence"] <= 1 for c in cases)
    scores = [c["priority_score"] for c in cases]
    assert scores == sorted(scores, reverse=True)


def test_recovery_is_drafted_for_approval_and_executed_only_after_approval(hunt):
    e, svc = hunt["e"], hunt["svc"]
    first = svc.scan(TENANT)
    assert first["new"] == first["detected"] and svc.mock_ap_requests(TENANT) == []  # nothing sent before approval
    drafts = [a for a in e.governance.list_approvals(TENANT, "pending") if a["payload"]["kind"] == "leakage_recovery"]
    assert len(drafts) == first["detected"]
    hold = next(a for a in drafts if a["payload"]["title"].startswith("Hold the payment"))
    claw = next(a for a in drafts if a["payload"]["title"].startswith("Request a refund of duplicate"))
    reject = next(a for a in drafts if a["payload"]["title"].startswith("Reduce unused seats"))
    for a, ok in ((hold, True), (claw, True), (reject, False)):
        svc.decided(TENANT, e.governance.decide(TENANT, a["approval_id"], ok, "ap-manager", None))
    requests = {r["case_id"]: r for r in svc.mock_ap_requests(TENANT)}
    assert requests[hold["payload"]["case_id"]]["status"] == "payment_blocked"
    assert requests[claw["payload"]["case_id"]]["status"] == "submitted_to_supplier" and len(requests) == 2
    status = {c["case_id"]: c["status"] for c in svc.cases(TENANT)}
    assert status[hold["payload"]["case_id"]] == "executed" and status[reject["payload"]["case_id"]] == "dismissed"
    again = svc.scan(TENANT)  # always-running sweeps update cases; they never duplicate them or their drafts
    assert again["new"] == 0 and len([a for a in e.governance.list_approvals(TENANT, "all")
                                      if a["payload"]["kind"] == "leakage_recovery"]) == first["detected"]
    summary = svc.summary(TENANT)
    assert summary["executed"] == 2 and summary["recoverable_usd"] > 0 and len(summary["by_type"]) == 5


def test_api_exposes_scan_summary_cases_and_mock_ap(hunt):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    original = api.engine
    api.engine = lambda: hunt["e"]
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": TENANT, "X-User-Id": "analyst"}
        assert c.post("/leakage/scan", headers=h).json()["detected"] >= 30
        cases = c.get("/leakage/cases", headers=h).json()
        assert cases[0]["priority_score"] >= cases[-1]["priority_score"] and cases[0]["type_label"]
        assert c.get("/leakage/summary", headers=h).json()["cases"] == len([x for x in cases if x["status"] != "resolved"])
        assert isinstance(c.get("/mock-ap/requests", headers=h).json(), list)
    finally:
        api.engine = original
