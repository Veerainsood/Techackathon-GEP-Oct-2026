"""State-based policies by region, the state check, and HubSpot-triggered autopilot with guardrails (own tenant)."""

import dataclasses

import pytest

T = "t_policy"


@pytest.fixture(scope="module")
def e(pg_uri):
    from backend.config import Settings
    from backend.engine import build_engine

    eng = build_engine(dataclasses.replace(Settings(), tenants=[T]), llm_providers=(None,) * 5)
    for table in ("policies", "policy_states", "policy_events", "autopilot_actions", "autopilot_contacts", "autopilot_settings",
                  "approvals", "guardrails"):
        eng.policies._exec(f"DELETE FROM backend_runtime.{table} WHERE tenant_id = %s", (T,))
    for p in eng.policies.list(T):  # start from no policies (the demo defaults are created on first read)
        eng.policies.delete(T, p["policy_id"], "test")
    return eng


def policy(**kw):
    from backend.policies.engine import PolicyIn

    return PolicyIn(**kw)


def test_admission_rejects_a_policy_that_leaves_no_allowed_value(e):
    from backend.policies.engine import PolicyRejected

    e.policies.save(T, policy(name="Contract states", collection="pending_contracts", variable="execution_status",
                              allowed_values=["Draft", "Counterpart_Pending", "Fully_Signed"]), "t", refresh=False)
    with pytest.raises(PolicyRejected) as exc:
        e.policies.save(T, policy(name="EMEA only terminated", collection="pending_contracts", region="EMEA", variable="execution_status",
                                  allowed_values=["Terminated"]), "t", refresh=False)
    assert exc.value.conflicts == ["Contract states"]
    e.policies.save(T, policy(name="TCV floor", collection="pending_contracts", region="APAC", variable="contracted_tcv_usd", min_value=500_000),
                    "t", refresh=False)
    with pytest.raises(PolicyRejected):  # at least 500k and at most 100k in Asia-Pacific: nothing would be allowed
        e.policies.save(T, policy(name="TCV cap", collection="pending_contracts", variable="contracted_tcv_usd", max_value=100_000), "t", refresh=False)
    for p in e.policies.list(T):
        e.policies.delete(T, p["policy_id"], "t")


def test_evaluation_intersects_global_and_regional_policies(e):
    e.policies.save(T, policy(name="States", collection="pending_contracts", variable="execution_status",
                              allowed_values=["Draft", "Counterpart_Pending", "Fully_Signed"], hitl_values=["Fully_Signed"]), "t", refresh=False)
    e.policies.save(T, policy(name="Americas cap", collection="pending_contracts", region="AMER", variable="contracted_tcv_usd", max_value=1_000_000),
                    "t", refresh=False)
    big = {"record_id": "X", "region": "AMER", "execution_status": "Counterpart_Pending", "contracted_tcv_usd": 2_000_000}
    assert e.policies.evaluate(T, "pending_contracts", big)["decision"] == "blocked"
    assert e.policies.evaluate(T, "pending_contracts", {**big, "region": "EMEA"})["decision"] == "allowed"  # the cap is Americas only
    signed = e.policies.evaluate(T, "pending_contracts", {**big, "region": "EMEA"}, {"execution_status": "Fully_Signed"})
    assert signed["decision"] == "hitl" and signed["hitl"][0]["policy"] == "States"
    assert e.policies.evaluate(T, "pending_invoices", {"record_id": "I", "region": "AMER"})["decision"] == "no_policy"
    for p in e.policies.list(T):
        e.policies.delete(T, p["policy_id"], "t")


def test_state_check_records_violations_and_their_clearing(e):
    rows = e.policies.records(T, "pending_invoices")
    region = max({r["region"] for r in rows}, key=lambda g: sum(1 for r in rows if r["region"] == g))
    top = max(float(r["open_balance_usd"]) for r in rows if r["region"] == region)
    p = e.policies.save(T, policy(name="Balance cap", collection="pending_invoices", region=region, variable="open_balance_usd",
                                  max_value=top - 1), "t", refresh=False)
    out = e.policies.refresh(T, "pending_invoices")  # (a background re-check after an earlier change may have counted them first)
    assert out["pending_invoices"]["records"] == len(rows)
    bad = e.policies.states(T, "pending_invoices", "blocked")
    assert bad and all(s["region"] == region and s["violations"][0]["policies"] == ["Balance cap"] for s in bad)
    assert next(s for s in e.policies.summary(T) if s["name"] == "Balance cap")["violations"] == len(bad)
    e.policies.save(T, policy(name="Balance cap", collection="pending_invoices", region=region, variable="open_balance_usd", max_value=top + 1),
                    "t", policy_id=p["policy_id"], refresh=False)
    e.policies.refresh(T, "pending_invoices")
    assert e.policies.states(T, "pending_invoices", "blocked") == []
    kinds = [x["kind"] for x in e.policies.events(T)]
    assert "violation" in kinds and "violation_cleared" in kinds
    e.policies.delete(T, p["policy_id"], "t")


def test_hubspot_change_leads_to_blocked_automatic_and_approved_actions(e):
    contracts = e.policies.records(T, "pending_contracts")
    group = max({c["customer_id"] for c in contracts}, key=lambda g: sum(1 for c in contracts if c["customer_id"] == g))
    mine = sorted([c for c in contracts if c["customer_id"] == group], key=lambda c: -float(c["contracted_tcv_usd"]))
    contact = {"contact_id": "hs-1", "email": "cfo@example.com", "firstname": "Ada", "lastname": "Byron", "conglomerate_id": group,
               "crm_account_name": mine[0]["customer"], "stage": "customer"}
    # no policy: nothing is done
    acts = e.autopilot.handle(T, contact, "lead", "customer", simulated=True)
    assert acts and all(a["decision"] == "skipped" for a in acts if a["action"] == "sign_contract")
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    e.policies.save(T, policy(name="States", collection="pending_contracts", variable="execution_status",
                              allowed_values=["Draft", "Counterpart_Pending", "Fully_Signed"]), "t", refresh=False)
    region = mine[0]["region"]
    cap = e.policies.save(T, policy(name="Value cap", collection="pending_contracts", region=region, variable="contracted_tcv_usd",
                                    max_value=float(mine[0]["contracted_tcv_usd"]) - 1), "t", refresh=False)
    e.governance.ensure_guardrails(T)
    e.governance.set_mode(T, "sign-contracts", "auto", "t")
    acts = {a["record_id"]: a for a in e.autopilot.handle(T, contact, "lead", "customer", simulated=True) if a["action"] == "sign_contract"}
    assert acts[mine[0]["record_id"]]["decision"] == "blocked" and "Value cap" not in acts[mine[0]["record_id"]]["reason"]  # reason states the bound
    for c in mine[1:]:
        assert acts[c["record_id"]]["decision"] == ("executed" if float(c["contracted_tcv_usd"]) < float(mine[0]["contracted_tcv_usd"]) else "blocked")
    assert not e.autopilot.handle(T, contact, "lead", "customer", simulated=True) or all(
        a["decision"] != "executed" for a in e.autopilot.handle(T, contact, "lead", "customer", simulated=True))  # done once, not again

    # Ask me: waits for a person; approval re-checks the policies; a policy tightened meanwhile blocks it
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    e.policies.delete(T, cap["policy_id"], "t")
    e.governance.set_mode(T, "sign-contracts", "approve", "t")
    waiting = [a for a in e.autopilot.handle(T, contact, "lead", "customer", simulated=True) if a["action"] == "sign_contract"]
    assert waiting and all(a["decision"] == "awaiting_approval" for a in waiting)
    first, rest = waiting[0], waiting[1:]
    row = e.governance.decide(T, first["action_id"], True, "approver", None)
    assert e.autopilot.decided(T, row)["decision"] == "executed"
    if rest:
        e.policies.save(T, policy(name="Tight cap", collection="pending_contracts", variable="contracted_tcv_usd", max_value=1), "t", refresh=False)
        row = e.governance.decide(T, rest[0]["action_id"], True, "approver", None)
        assert e.autopilot.decided(T, row)["decision"] == "blocked"

    # Never refuses; the emergency stop pauses everything
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    for p in [x for x in e.policies.list(T) if x["name"] == "Tight cap"]:
        e.policies.delete(T, p["policy_id"], "t")
    e.governance.set_mode(T, "sign-contracts", "never", "t")
    assert {a["decision"] for a in e.autopilot.handle(T, contact, "lead", "customer", simulated=True) if a["action"] == "sign_contract"} == {"refused"}
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    e.autopilot.set_paused(T, True, "t")
    assert {a["decision"] for a in e.autopilot.handle(T, contact, "lead", "customer", simulated=True)} == {"paused"}
    e.autopilot.set_paused(T, False, "t")


def test_the_same_change_arriving_twice_at_once_acts_once(e):
    import threading

    contracts = e.policies.records(T, "pending_contracts")
    group = contracts[0]["customer_id"]
    contact = {"contact_id": "hs-2", "email": "x@example.com", "conglomerate_id": group, "crm_account_name": "X", "stage": "customer"}
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    e.policies.save(T, policy(name="Any state", collection="pending_contracts", variable="execution_status",
                              allowed_values=["Draft", "Counterpart_Pending", "Fully_Signed"]), "t", refresh=False)
    e.governance.set_mode(T, "sign-contracts", "auto", "t")
    runs = [threading.Thread(target=e.autopilot.handle, args=(T, contact, "lead", "customer", True)) for _ in range(2)]
    for r in runs:
        r.start()
    for r in runs:
        r.join()
    done = e.policies._exec("SELECT record_id, count(*) AS n FROM backend_runtime.autopilot_actions WHERE tenant_id = %s AND decision = 'executed' "
                            "GROUP BY record_id", (T,), fetch="all")
    assert done and all(r["n"] == 1 for r in done)
    for p in [x for x in e.policies.list(T) if x["name"] == "Any state"]:
        e.policies.delete(T, p["policy_id"], "t")


def test_only_stage_changes_seen_after_priming_trigger_autopilot(e, monkeypatch):
    contracts = e.policies.records(T, "pending_contracts")
    group = contracts[0]["customer_id"]
    people = [{"contact_id": "c1", "email": "a@x.com", "stage": "lead", "conglomerate_id": group, "crm_account_id": "CRM-1", "crm_account_name": "X"}]
    monkeypatch.setattr(e.autopilot, "_contacts", lambda tenant: [dict(p) for p in people])
    e.policies._exec("DELETE FROM backend_runtime.autopilot_contacts WHERE tenant_id = %s", (T,))
    e.policies._exec("DELETE FROM backend_runtime.autopilot_actions WHERE tenant_id = %s", (T,))
    e.autopilot.prime(T)
    assert e.autopilot.on_hubspot_event(T)["transitions"] == []
    people[0]["stage"] = "customer"
    out = e.autopilot.on_hubspot_event(T)
    assert out["transitions"] == [{"contact": "a@x.com", "from": "lead", "to": "customer"}] and out["actions"]
    assert e.autopilot.on_hubspot_event(T)["transitions"] == []  # the new stage is remembered


def test_api_policies_catalog_conflict_and_status(e):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    original = api.engine
    api.engine = lambda: e
    try:
        c = TestClient(api.app)
        h = {"X-Tenant-Id": T, "X-User-Id": "admin"}
        cat = c.get("/policies/catalog", headers=h).json()
        contracts = next(x for x in cat["collections"] if x["id"] == "pending_contracts")
        status = next(f for f in contracts["fields"] if f["id"] == "execution_status")
        assert "Fully_Signed" in status["values"] and {r["code"] for r in cat["regions"]} == {"ALL", "AMER", "EMEA", "APAC"}
        body = {"name": "Only drafts", "collection": "pending_contracts", "variable": "execution_status", "allowed_values": ["Draft"]}
        assert c.post("/policies", headers=h, json=body).status_code == 200
        r = c.post("/policies", headers=h, json={**body, "name": "Only signed", "allowed_values": ["Fully_Signed"]})
        assert r.status_code == 409 and "Only drafts" in r.json()["detail"]["message"]
        assert any(p["name"] == "Only drafts" for p in c.get("/policies", headers=h).json())
        # guardrails: only actions whose records a policy covers ("Only drafts" covers pending contracts)
        rows = c.get("/guardrails", headers=h).json()
        assert [g["rule_id"] for g in rows] == ["sign-contracts"] and "Only drafts" in rows[0]["limit"]
        assert c.post("/autopilot/status", headers=h, json={"paused": True}).json() == {"paused": True}
        assert c.get("/autopilot/status", headers=h).json() == {"paused": True}
        c.post("/autopilot/status", headers=h, json={"paused": False})
    finally:
        api.engine = original
