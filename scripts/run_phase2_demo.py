"""Phase 2 end-to-end demo: register sources, ingest (mock) HubSpot and Intune through the common pipeline,
generate skill reference files, build the ontology, run both agents, inspect live query outputs, adjudicate,
answer with lineage and provenance, and export.

The HubSpot and Intune APIs are the deterministic MOCK fixtures in backend/ingestion/mocks.py unless real
credentials are configured with --live (BACKEND_HUBSPOT_ACCESS_TOKEN / BACKEND_INTUNE_* variables).
Reasoning uses the configured LLM if BACKEND_LLM_* is set; otherwise the explicit deterministic offline mode.

Usage: python scripts/run_phase2_demo.py [--live]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.engine import build_engine  # noqa: E402
from backend.gateway.registry import AccessContext  # noqa: E402
from backend.ingestion.hubspot import HubSpotSourceAdapter  # noqa: E402
from backend.ingestion.intune import MicrosoftIntuneSourceAdapter  # noqa: E402
from backend.ingestion.mocks import MockGraphAPI, MockHubSpotAPI  # noqa: E402

TENANT = "demo"


def show(state):
    d = state["decision"]
    print(f"  ANSWER   {d['final_answer'][:300]}")
    print(f"  STATUS   {d['verification_status']} | {d['selected_path']} | {d['agreement_status']} | {state['total_ms']:.0f} ms")
    for k in ("explorer_candidate", "hercules_candidate"):
        c = state.get(k)
        if c:
            print(f"  {c['agent_name']:<9}{c['reasoning_mode']:<22} rows={len(c['observed_results'])} "
                  f"provenance={c['result_provenance'].get('mode')} sql={c['query_or_plan'][:150]}")
    for f in d["validation_findings"]:
        if f["severity"] != "info" or not f["passed"]:
            print(f"  FINDING  [{f['severity']}] {f['validator_name']}: {f['message'][:140]}")
    for x in state.get("exports", []):
        print(f"  EXPORT   {x['path']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use real HubSpot/Intune credentials from the environment")
    args = ap.parse_args()
    e = build_engine()
    print(f"reasoning: explorer={'llm' if e.explorer.llm else 'deterministic_offline'}, "
          f"hercules={'llm' if e.hercules.llm else 'deterministic_offline'}")
    for sid in ("finance_pg", "uploads"):
        e.gateway.grant(sid, TENANT)
    ctx = AccessContext(tenant_id=TENANT)
    e.prepare_source(ctx, "finance_pg")
    e.prepare_source(ctx, "uploads")

    if args.live:
        hubspot, intune, label = HubSpotSourceAdapter(), MicrosoftIntuneSourceAdapter(), "LIVE"
    else:
        names = [r[0] for r in e.gateway.execute_query(ctx, "finance_pg",
                 "SELECT conglomerate_name FROM master_ref.client_conglomerates ORDER BY conglomerate_id LIMIT 25").rows and
                 [(r["conglomerate_name"],) for r in e.gateway.execute_query(ctx, "finance_pg",
                  "SELECT conglomerate_name FROM master_ref.client_conglomerates ORDER BY conglomerate_id LIMIT 25").rows]]
        hub, graph = MockHubSpotAPI(company_names=names), MockGraphAPI()
        hubspot = HubSpotSourceAdapter(access_token=MockHubSpotAPI.TOKEN, transport=hub.transport(), sleep=lambda s: None)
        intune = MicrosoftIntuneSourceAdapter(graph.tenant_id, graph.client_id, graph.client_secret, transport=graph.transport(),
                                              sleep=lambda s: None)
        label = "MOCK FIXTURE"
    for sid, conn in (("hubspot", hubspot), ("intune", intune)):
        rep = e.ingestion.ingest(ctx, conn, sid, name=f"{sid} ({label})")
        print(f"\n[{label}] ingest {sid}: ok={rep.ok}")
        for s in rep.steps:
            extra = {k: v for k, v in s.items() if k not in ("step", "status", "ms")}
            print(f"  {s['step']:<26} {s['status']:<7} {s['ms']:>8} ms  {json.dumps(extra, default=str)[:140]}")
        caps = conn.capabilities()
        print(f"  capabilities: live_query={caps.live_query} api={caps.api_version}; permissions={caps.required_permissions}")

    tb = e.toolbox()
    print("\nskills:", [s["skill_id"] for s in tb.list_skills(ctx)])
    for sid in ("finance_pg", "uploads", "hubspot", "intune"):
        cur = e.contexts.current(TENANT, sid)
        print(f"  {sid:<11} {len(tb.list_collection_contexts(ctx, sid)):>3} collection files, version {cur['version']}")
    gc = json.loads((e.contexts.root / "generated" / TENANT / "global-context.json").read_text(encoding="utf-8"))
    print(f"  global context: {gc['compression']['raw_tokens']} -> {gc['compression']['compressed_tokens']} tokens; "
          f"inclusion {gc['inclusion']['collections_included']}/{gc['inclusion']['collections_total']} collections")
    xs = [ed["properties"] for ed in e.kg.edges(tenant=TENANT) if ed["properties"].get("kind") == "cross_source_candidate"]
    for p in xs[:3]:
        print(f"  cross-source candidate (unverified): {p['left_source']}:{p['left_collection']}.{p['left_column']} ~ "
              f"{p['right_source']}:{p['right_collection']}.{p['right_column']} ({p['matches']} matches, {p['basis']})")

    questions = [
        (None, "What is the total open balance in USD by settlement status?"),
        ({"hubspot"}, "How many contacts have lifecycle stage customer?"),
        ({"intune"}, "How many managed devices have compliance state noncompliant?"),
        (None, "What was our revenue last year?"),
    ]
    for sources, q in questions:
        print(f"\nQ: {q}  [sources: {sorted(sources) if sources else 'all authorized'}]")
        qctx = AccessContext(tenant_id=TENANT, allowed_sources=sources)
        show(e.ask(q, ctx=qctx, use_memory=False, export_formats=["excel", "json"]))

    fed = tb.execute_federated_query(ctx, {
        "fin": {"source_id": "finance_pg", "query": "SELECT conglomerate_name, primary_industry_code FROM master_ref.client_conglomerates"},
        "crm": {"source_id": "hubspot", "query": "SELECT name, industry, annualrevenue FROM companies"}},
        "SELECT fin.primary_industry_code AS finance_industry, crm.industry AS crm_industry, COUNT(*) AS n "
        "FROM fin JOIN crm ON lower(crm.name) = lower(fin.conglomerate_name) GROUP BY 1, 2 ORDER BY n DESC LIMIT 5")
    print("\nfederated finance x CRM (name join, candidate only):", fed["rows"], "| provenance:",
          {k: v["mode"] for k, v in fed["provenance"].items()})


if __name__ == "__main__":
    main()
