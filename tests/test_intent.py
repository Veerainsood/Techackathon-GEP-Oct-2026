"""Intent agent: direct replies skip the investigation, data questions keep the dual path, the LLM path uses tools and
returns a schema-checked decision, and the brief reaches the investigating agents."""

import json

import pytest

from backend.gateway.registry import AccessContext
from backend.llm.base import LLMResponse
from backend.llm.scripted import ScriptedProvider, last_tool_result, tool


@pytest.fixture(scope="module")
def offline_engine(pg_uri, uploads_dir):
    from backend.engine import build_engine

    return build_engine(llm_providers=(None, None, None, None))


def nodes(state):
    return [t["node"] for t in state["trace"]]


def test_capability_question_is_answered_directly_without_agents_or_exports(offline_engine):
    st = offline_engine.ask("what can you do?", use_memory=False, export_formats=["csv"])
    d = st["decision"]
    assert st["intent"]["route"] == "answer" and d["selected_path"] == "Direct_Response"
    assert d["verification_status"] == "not_applicable" and "Finance warehouse" in d["final_answer"]
    assert "explorer" not in nodes(st) and "hercules" not in nodes(st) and st["exports"] == []


def test_catalog_question_lists_sources(offline_engine):
    st = offline_engine.ask("Which data sources do you have access to?", use_memory=False)
    assert st["intent"]["route"] == "answer" and "hubspot" in st["decision"]["final_answer"]


def test_data_question_still_runs_both_agents(offline_engine):
    events = []
    st = offline_engine.ask("How many invoices have settlement status OVERDUE?", use_memory=False, on_event=events.append)
    assert st["intent"]["route"] == "investigate"
    assert {"explorer", "hercules", "verify", "adjudicate"} <= set(nodes(st))
    assert st["decision"]["verification_status"] == "verified_fact"
    started = {e["node"] for e in events if e.get("event") == "node" and e["status"] == "started"}
    finished = {e["node"] for e in events if e.get("event") == "node" and e["status"] == "finished"}
    assert started == finished  # a node's own status field (e.g. agreement) never hides that it finished
    assert next(e for e in events if e.get("node") == "adjudicate" and e["status"] == "finished")["outcome"] == "exact_match"


def _intent_policy(final):
    """Look something up first, then submit the given decision."""
    def policy(system, messages, tools):
        if last_tool_result(messages) is None:
            return tool("lookup_knowledge", gap="what revenue definitions exist", query="revenue")
        return tool("submit_intent", **final)
    return policy


def test_llm_intent_uses_tools_and_asks_a_clarifying_question(pg_uri, uploads_dir):
    from backend.engine import build_engine

    intent = ScriptedProvider(_intent_policy({"route": "clarify", "reason": "revenue has two definitions",
                                              "clarifying_question": "Which revenue do you mean?",
                                              "options": ["Recognized GL revenue", "Invoiced amount"]}))
    e = build_engine(llm_providers=(None, None, None, intent))
    events = []
    st = e.ask("What was our revenue last year?", use_memory=False, on_event=events.append)
    assert st["intent"]["route"] == "clarify" and st["intent"]["mode"] == "llm"
    assert st["decision"]["selected_path"] == "Clarification_Requested" and "Invoiced amount" in st["decision"]["final_answer"]
    assert any(ev.get("agent") == "Intent" and ev.get("event") == "tool" and ev.get("tool") == "lookup_knowledge" for ev in events)
    assert "resolve_sources" not in nodes(st)


def test_incomplete_decision_is_sent_back_and_brief_reaches_explorer(pg_uri, uploads_dir):
    from backend.engine import build_engine

    calls = {"n": 0}

    def intent_policy(system, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool("submit_intent", route="investigate", reason="data question")  # missing brief -> rejected
        return tool("submit_intent", route="investigate", reason="data question",
                    brief={"interpreted_question": "count OVERDUE sales invoices", "sources": ["finance_pg"],
                           "collections": ["ledger_erp.sales_invoices"], "notes": "settlement_status holds OVERDUE"})

    def explorer_policy(system, messages, tools):
        return tool("submit_candidate", interpreted_question="count overdue invoices", source_id="finance_pg",
                    sql="SELECT COUNT(*) AS value FROM ledger_erp.sales_invoices WHERE settlement_status = 'OVERDUE'",
                    metric_interpretation="count", answer_summary="overdue invoices")

    explorer = ScriptedProvider(explorer_policy)
    e = build_engine(llm_providers=(explorer, None, None, ScriptedProvider(intent_policy)))
    st = e.ask("How many invoices are overdue?", use_memory=False)
    assert calls["n"] == 2 and st["intent"]["brief"]["collections"] == ["ledger_erp.sales_invoices"]
    first_user_message = json.dumps(explorer.requests[0]["messages"][0])
    assert "Intent agent's brief" in first_user_message and "settlement_status holds OVERDUE" in first_user_message


def test_api_stream_reports_the_intent_and_skips_agents_for_greetings(offline_engine):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    original = api.engine
    api.engine = lambda: offline_engine
    try:
        c = TestClient(api.app)
        with c.stream("POST", "/ask/stream", json={"question": "hi", "use_memory": False}) as r:
            events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
        result = events[-1]["result"]
        assert result["intent"]["route"] == "answer" and result["candidates_summary"] == {}
        ran = {e["node"] for e in events if e.get("event") == "node"}
        assert "understand_intent" in ran and "explorer" not in ran
        assert any(e.get("agent") == "Intent" and e.get("event") == "decision" for e in events)
    finally:
        api.engine = original
