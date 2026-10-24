"""Intune and HubSpot connectors against deterministic mock APIs (live integration is NOT verified here)."""

import json
from pathlib import Path

import duckdb
import pytest

from backend.contracts.models import EdgeType
from backend.gateway.registry import AccessContext, AccessDenied
from backend.ingestion.base import AuthenticationError
from backend.ingestion.hubspot import HubSpotSourceAdapter, verify_webhook_v3
from backend.ingestion.intune import MicrosoftIntuneSourceAdapter
from backend.ingestion.mocks import MockGraphAPI, MockHubSpotAPI, webhook_signature
from backend.ingestion.pipeline import IngestionPipeline

TENANT = "t_connectors"
NOSLEEP = lambda s: None  # noqa: E731


@pytest.fixture(scope="module")
def env(pg_uri, uploads_dir, tmp_path_factory, generator):
    from backend.engine import build_engine

    e = build_engine(llm_providers=(None, None))
    e.kg.delete_tenant(TENANT)
    e.contexts.delete_tenant(TENANT)
    e.gateway.grant("finance_pg", TENANT)
    ctx = AccessContext(tenant_id=TENANT)
    e.prepare_source(ctx, "finance_pg")
    pipe = IngestionPipeline(e, snapshot_root=tmp_path_factory.mktemp("snapshots"))
    graph = MockGraphAPI()
    names = [g["conglomerate_name"] for g in generator.frames["master_ref.client_conglomerates"].to_dict("records")][:25]
    hub = MockHubSpotAPI(company_names=names)
    intune = MicrosoftIntuneSourceAdapter(graph.tenant_id, graph.client_id, graph.client_secret, transport=graph.transport(),
                                          sleep=NOSLEEP)
    hubspot = HubSpotSourceAdapter(access_token=MockHubSpotAPI.TOKEN, transport=hub.transport(), sleep=NOSLEEP)
    reports = {"intune": pipe.ingest(ctx, intune, "intune", name="Intune (mock)"),
               "hubspot": pipe.ingest(ctx, hubspot, "hubspot", name="HubSpot (mock)")}
    yield dict(engine=e, ctx=ctx, pipe=pipe, graph=graph, hub=hub, intune=intune, hubspot=hubspot, reports=reports)
    e.kg.delete_tenant(TENANT)
    e.contexts.delete_tenant(TENANT)


def _q(env, source, sql):
    r = env["engine"].gateway.execute_query(env["ctx"], source, sql)
    assert r.succeeded, r.error_message
    return r


# ------------------------------------------------------------------ Intune
def test_intune_paginates_throttles_and_profiles(env):
    rep = env["reports"]["intune"]
    assert rep.ok, rep.failed_objects
    assert rep.objects["managed_devices"]["total"] == 230 and rep.objects["managed_devices"]["pages"] >= 3
    assert env["intune"].api.stats["throttled"] == 1 and env["intune"].api.stats["retries"] >= 1
    r = _q(env, "intune", 'SELECT COUNT(*) AS n, COUNT(DISTINCT policy_id) AS p FROM "compliance_policy_device_statuses"')
    assert r.rows[0] == {"n": 230, "p": 4}
    assert r.provenance["mode"] == "snapshot" and r.provenance["synced_at"]
    cols = {c.name for c in env["engine"].gateway.adapter(env["ctx"], "intune").describe_collection("managed_devices").columns}
    assert {"hardware_information__total_storage_space", "compliance_state", "_backend_present", "_backend_archived"} <= cols
    refs = env["engine"].kg.edges(EdgeType.REFERENCES, source_id="intune", tenant=TENANT)
    assert any(e["properties"]["child_col"] == "policy_id" and e["verification_status"] == "verified_fact" for e in refs)


def test_intune_reference_files_hide_credentials_and_pii(env):
    doc = env["engine"].toolbox().load_collection_context(env["ctx"], "intune", "managed_devices", include_value_profile=True)
    text = doc["content"] + doc["value_profile"]
    assert 'data_mode: "snapshot"' in doc["content"]
    assert "user001@contoso-sim.example" not in text and "us***" in text
    root = env["engine"].contexts.root
    blob = "".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file() and TENANT in str(p))
    for secret in (env["graph"].client_secret, env["graph"].tenant_id, MockGraphAPI.TOKEN, MockHubSpotAPI.TOKEN):
        assert secret not in blob


def test_intune_authentication_errors_are_redacted(env):
    g = MockGraphAPI()
    bad = MicrosoftIntuneSourceAdapter(g.tenant_id, g.client_id, "wrong-secret-value", transport=g.transport(), sleep=NOSLEEP)
    rep = env["pipe"].ingest(AccessContext(tenant_id="t_conn_bad"), bad, "intune_bad")
    assert not rep.ok and rep.steps[-1]["step"] == "authenticate" and "AADSTS7000215" in rep.steps[-1]["error"]
    assert "wrong-secret-value" not in json.dumps(rep.steps)
    with pytest.raises(AuthenticationError):
        MicrosoftIntuneSourceAdapter("", "", "")
    env["engine"].gateway.unregister("intune_bad")


def test_intune_failure_recovery_keeps_other_objects(env, tmp_path):
    from backend.engine import build_engine  # noqa: F401

    g = MockGraphAPI(throttle_once=False)
    g.fail_paths["/deviceManagement/detectedApps"] = 50
    conn = MicrosoftIntuneSourceAdapter(g.tenant_id, g.client_id, g.client_secret, transport=g.transport(), sleep=NOSLEEP)
    ctx = AccessContext(tenant_id="t_conn_recover")
    pipe = IngestionPipeline(env["engine"], snapshot_root=tmp_path)
    try:
        rep = pipe.ingest(ctx, conn, "intune_r")
        assert "detected_apps" in rep.failed_objects and rep.objects["managed_devices"]["total"] == 230
        state = pipe._store("t_conn_recover", "intune_r").state("detected_apps")
        assert state["status"] == "failed"
        g.fail_paths.clear()
        rep2 = pipe.sync(ctx, "intune_r")
        assert rep2.ok and rep2.objects["detected_apps"]["total"] == 60
    finally:
        env["engine"].gateway.unregister("intune_r")
        env["engine"].kg.delete_tenant("t_conn_recover")
        env["engine"].contexts.delete_tenant("t_conn_recover")


def test_intune_report_export_job(env, tmp_path):
    g = MockGraphAPI(throttle_once=False)
    conn = MicrosoftIntuneSourceAdapter(g.tenant_id, g.client_id, g.client_secret, resources=["report:DevicesWithInventory"],
                                        transport=g.transport(), sleep=NOSLEEP)
    res = conn.fetch(conn.discover_objects()[0], None, full=True)
    assert len(res.records) == 50 and "ComplianceState" in res.records[0]
    assert any(r.startswith("POST /v1.0/deviceManagement/reports/exportJobs") for r in g.requests)


# ------------------------------------------------------------------ HubSpot
def test_hubspot_dynamic_properties_custom_objects_archives(env):
    rep = env["reports"]["hubspot"]
    assert rep.ok, rep.failed_objects
    ad = env["engine"].gateway.adapter(env["ctx"], "hubspot")
    assert {"companies", "contacts", "deals", "products", "line_items", "subscriptions", "deal_pipelines",
            "deal_pipeline_stages", "assoc__companies__contacts"} <= set(ad.discover_collections())
    comp = {c.name: c for c in ad.describe_collection("companies").columns}
    assert "customer_tier__c" in comp and "Gold" in comp["customer_tier__c"].description
    types = {c.name: c.data_type for c in ad.describe_collection("deals").columns}
    assert types["amount"] == "DOUBLE" and types["hs_is_closed_won"] == "BOOLEAN"
    assert _q(env, "hubspot", 'SELECT COUNT(*) AS n FROM "contacts" WHERE "_backend_archived"').rows[0]["n"] == 3
    assert _q(env, "hubspot", 'SELECT COUNT(*) AS n FROM "subscriptions"').rows[0]["n"] == 20


def test_hubspot_associations_keep_type_and_direction(env):
    r = _q(env, "hubspot", "SELECT association_label, direction, COUNT(*) AS n FROM assoc__companies__contacts "
                           "GROUP BY 1, 2 ORDER BY 1 NULLS FIRST")
    labels = {row["association_label"] for row in r.rows}
    assert "Billing contact" in labels and all(row["direction"] == "companies->contacts" for row in r.rows)
    edges = env["engine"].kg.edges(EdgeType.REFERENCES, source_id="hubspot", tenant=TENANT)
    assoc = [e for e in edges if e["properties"]["child"] == "assoc__companies__contacts"]
    assert {e["properties"]["role"] for e in assoc} == {"from", "to"}
    assert all(e["properties"]["direction"] == "companies->contacts" and e["verification_status"] == "verified_fact" for e in assoc)
    stage = [e for e in edges if e["properties"]["child"] == "deals" and e["properties"]["child_col"] == "dealstage"]
    assert stage and stage[0]["verification_status"] == "verified_fact"


def test_hubspot_incremental_sync_and_schema_evolution(env):
    hub, ctx = env["hub"], env["ctx"]
    deal = hub.objects["deals"][0]["id"]
    hub.touch("deals", deal, amount="999")
    hub.add_property("deals", "forecast_bucket__c", "Forecast bucket", "string", {deal: "commit"})
    env["hubspot"]._meta.clear()
    rep = env["pipe"].sync(ctx, "hubspot")
    # GTE checkpoint is inclusive (at-least-once): records on the previous boundary are re-read idempotently
    assert rep.ok and rep.objects["deals"]["mode"] == "incremental" and 1 <= rep.objects["deals"]["upserted"] < 10
    r = _q(env, "hubspot", f"SELECT amount, forecast_bucket__c FROM deals WHERE id = '{deal}'")
    assert r.rows == [{"amount": 999.0, "forecast_bucket__c": "commit"}]
    assert _q(env, "hubspot", "SELECT COUNT(*) AS n FROM deals").rows[0]["n"] == 60
    assert any("/crm/v3/objects/deals/search" in req for req in hub.requests)


def test_hubspot_webhooks_signature_and_queue(env):
    secret, body, uri, ts = "hs-secret", '[{"objectId": 1}]', "https://backend.example/webhooks/hubspot/hubspot", "1761200000000"
    sig = webhook_signature(secret, "POST", uri, body, ts)
    assert verify_webhook_v3(secret, "POST", uri, body, ts, sig, now_ms=1761200001000)
    assert not verify_webhook_v3(secret, "POST", uri, body + " ", ts, sig, now_ms=1761200001000)
    assert not verify_webhook_v3(secret, "POST", uri, body, ts, sig, now_ms=1761200000000 + 10 * 60 * 1000)
    contact = env["hub"].objects["contacts"][10]["id"]
    store = env["pipe"]._store(TENANT, "hubspot")
    env["hubspot"].handle_webhook([{"objectId": int(contact), "subscriptionType": "contact.propertyChange", "occurredAt": 1}], store)
    env["hubspot"].pending_ids.clear()  # simulate a restart: only the persisted queue remains
    before = len(env["hub"].requests)
    rep = env["pipe"].sync(env["ctx"], "hubspot")
    assert rep.ok and any("/crm/v3/objects/contacts/batch/read" in r for r in env["hub"].requests[before:])
    assert not store.pending_changes("contacts")


def test_hubspot_oauth_refresh_and_failure():
    hub = MockHubSpotAPI()
    ok = HubSpotSourceAdapter(client_id="hs-client", client_secret="hs-secret", refresh_token="hs-refresh",
                              transport=hub.transport(), sleep=NOSLEEP)
    ok.authenticate()
    assert any(r == "POST /oauth/v1/token" for r in hub.requests)
    bad = HubSpotSourceAdapter(client_id="hs-client", client_secret="hs-secret", refresh_token="revoked",
                               transport=hub.transport(), sleep=NOSLEEP)
    with pytest.raises(AuthenticationError):
        bad.authenticate()


# ------------------------------------------------------------------ cross-source and isolation
def test_cross_source_candidates_are_never_asserted(env):
    edges = [e for e in env["engine"].kg.edges(EdgeType.MAPS_TO, tenant=TENANT) if e["properties"].get("kind") == "cross_source_candidate"]
    pair = [e for e in edges if {e["properties"]["left_source"], e["properties"]["right_source"]} == {"finance_pg", "hubspot"}
            and "conglomerate_name" in (e["properties"]["left_column"], e["properties"]["right_column"])]
    assert pair and pair[0]["properties"]["matches"] >= 20
    assert all(e["verification_status"] == "inferred_candidate" for e in edges)
    assert pair[0]["properties"]["basis"] == "normalized name overlap only"
    doc = env["engine"].toolbox().load_collection_context(env["ctx"], "finance_pg", "master_ref.client_conglomerates")
    assert "Cross-source candidate" in doc["content"] and "Not an identity assertion" in doc["content"]


def test_snapshot_sources_respect_tenant_isolation(env):
    other = AccessContext(tenant_id="someone_else")
    assert "hubspot" not in [s.source_id for s in env["engine"].gateway.sources(other)]
    with pytest.raises(AccessDenied):
        env["engine"].toolbox().load_collection_context(other, "hubspot", "companies")
    assert not env["engine"].gateway.execute_query(AccessContext(tenant_id=TENANT, denied_collections={"hubspot:contacts"}),
                                                   "hubspot", "SELECT * FROM contacts").succeeded


def test_dual_path_answers_from_snapshot_with_provenance(env):
    ctx = AccessContext(tenant_id=TENANT, allowed_sources={"intune"})
    truth = sum(1 for d in env["graph"].devices if d["complianceState"] == "noncompliant")
    state = env["engine"].ask("How many managed devices have compliance state noncompliant?", ctx=ctx, use_memory=False)
    d = state["decision"]
    assert d["verification_status"] == "verified_fact", d["final_answer"]
    assert state["explorer_candidate"]["structured_value"] == truth
    assert state["explorer_candidate"]["result_provenance"]["mode"] == "snapshot"


def test_federated_query_finance_and_crm(env):
    res = env["engine"].toolbox().execute_federated_query(env["ctx"], {
        "fin": {"source_id": "finance_pg", "query": "SELECT conglomerate_name FROM master_ref.client_conglomerates"},
        "crm": {"source_id": "hubspot", "query": "SELECT name, annualrevenue FROM companies"}},
        "SELECT COUNT(*) AS value FROM fin JOIN crm ON lower(crm.name) = lower(fin.conglomerate_name)")
    assert res["succeeded"] and res["rows"][0]["value"] == 25
    assert res["provenance"]["crm"]["mode"] == "snapshot" and res["provenance"]["fin"]["mode"] == "live"
