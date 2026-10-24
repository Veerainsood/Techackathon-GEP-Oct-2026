import operator
from pathlib import Path
from typing import Annotated, TypedDict

import pandas as pd
import pytest

from backend.agents.planner import FilterSpec, JoinSpec, QueryPlan, compile_sql
from backend.contracts.models import (
    AgentReasoningCandidate,
    EdgeType,
    NodeType,
    ValidationFinding,
    VerificationStatus,
)
from backend.gateway.registry import AccessContext
from backend.ontology.graph import PromotionRefused
from backend.skills.tools import ToolBox, ToolBudgetExceeded, ToolSession


@pytest.fixture(scope="session")
def engine(pg_uri, uploads_dir):
    from backend.engine import build_engine

    e = build_engine()
    for sid in ("finance_pg", "uploads"):
        e.builder.build(e.default_ctx, sid)
    return e


def _view(engine, colls):
    from backend.agents.catalog import view_from_described

    ctx = engine.default_ctx
    tb = ToolBox(engine.gateway, engine.kg, None, engine.settings.exports_dir)
    described = [tb.describe_collection(ctx, "finance_pg", c) for c in colls]
    adapter = engine.gateway.adapter(ctx, "finance_pg")
    return view_from_described("finance_pg", adapter.dialect, adapter.quote, described,
                               tb.relationship_graph(ctx, "finance_pg"), engine.packs)


# ------------------------------------------------------------------ ontology / knowledge graph
def test_ontology_discovers_structure_and_rules(engine):
    stats = engine.kg.stats()
    types = {n["type"] for n in stats["nodes"]}
    assert {"Source", "Collection", "Column", "Concept", "Metric", "Dimension", "ValueMapping"} <= types
    refs = engine.kg.edges(EdgeType.REFERENCES, source_id="finance_pg")
    assert len([e for e in refs if e["verification_status"] == "verified_fact"]) >= 50
    rules = {(e["properties"]["parent_col"], e["properties"]["child_col"])
             for e in engine.kg.edges(EdgeType.DERIVED_FROM, source_id="finance_pg") if e["properties"].get("rule")}
    assert ("open_balance_usd", "signed_amount_usd") in rules
    assert ("subtotal_amount_usd", "line_net_amount_usd") in rules
    inferred = engine.kg.edges(EdgeType.REFERENCES, source_id="uploads")
    assert any(not e["properties"]["declared"] and e["verification_status"] == "verified_fact" for e in inferred)


def test_promotion_requires_deterministic_evidence(engine):
    concept = engine.kg.nodes(NodeType.CONCEPT)[0]
    with pytest.raises(PromotionRefused):
        engine.kg.promote(concept["node_id"], ["ev:does-not-exist"])
    with pytest.raises(PromotionRefused):
        engine.kg.set_status(concept["node_id"], VerificationStatus.VERIFIED_FACT)
    assert engine.kg.get_node(concept["node_id"])["verification_status"] == "inferred_candidate"


def test_schema_change_marks_knowledge_stale(engine, tmp_path):
    from backend.gateway.file_adapter import FileSourceAdapter

    ctx = AccessContext(tenant_id="t_schema_change")
    engine.kg.delete_tenant("t_schema_change")
    csv = tmp_path / "ledger.csv"
    pd.DataFrame({"entry_id": [1, 2, 3], "amount_usd": [10.0, 20.0, 5.5], "region": ["A", "B", "A"]}).to_csv(csv, index=False)
    engine.gateway.register(FileSourceAdapter("tmp_src", [str(csv)]), tenants=["t_schema_change"])
    try:
        first = engine.builder.build(ctx, "tmp_src")
        assert not first.skipped and first.columns == 3
        assert engine.builder.build(ctx, "tmp_src").skipped  # unchanged fingerprint -> incremental no-op
        pd.DataFrame({"entry_id": [1, 2], "amount_usd": [10.0, 20.0]}).to_csv(csv, index=False)
        engine.gateway.unregister("tmp_src")
        engine.gateway.register(FileSourceAdapter("tmp_src", [str(csv)]), tenants=["t_schema_change"])
        second = engine.builder.build(ctx, "tmp_src")
        assert second.schema_version != first.schema_version and second.stale_marked["nodes"] > 0
        stale = engine.kg.nodes(source_id="tmp_src", tenant="t_schema_change", status=VerificationStatus.STALE_SCHEMA)
        assert any(n["label"].endswith(".region") for n in stale)
    finally:
        engine.gateway.unregister("tmp_src")
        engine.kg.delete_tenant("t_schema_change")


# ------------------------------------------------------------------ compiler
def test_compiler_compresses_and_respects_budget(engine):
    compiled = engine.compiled_context(engine.default_ctx, use_memory=False)
    s = compiled.compression_stats
    assert s.compression_ratio > 2 and s.token_reduction_pct > 50 and s.collection_count >= 39
    text, info = engine.compiler.render(compiled, "open receivables by customer", budget_tokens=5000)
    assert info["rendered_tokens"] <= 5000 and 0 < info["collections_included"] < info["collections_total"]


# ------------------------------------------------------------------ tools
def test_tool_session_requires_objective_and_budget(engine):
    tb = ToolBox(engine.gateway, engine.kg, None, engine.settings.exports_dir)
    s = ToolSession(tb, engine.default_ctx, "test", max_calls=2)
    with pytest.raises(ValueError):
        s.call("discover_sources", objective="", phase="DISCOVER")
    s.call("discover_sources", objective="list sources", phase="DISCOVER")
    hits = s.call("discover_collections", objective="find invoice tables", phase="DISCOVER", query="customer invoices open balance")
    assert any(h["collection"] == "ledger_erp.sales_invoices" for h in hits[:5])
    with pytest.raises(ToolBudgetExceeded):
        s.call("discover_sources", objective="again", phase="DISCOVER")
    assert [o.tool_name for o in s.observations] == ["discover_sources", "discover_collections"]


def test_value_search_uses_graph_then_live_data(engine):
    tb = ToolBox(engine.gateway, engine.kg, None, engine.settings.exports_dir)
    m = tb.search_column_values(engine.default_ctx, "finance_pg", "fully signed")
    assert m and m[0]["matched_value"] == "Fully_Signed" and m[0]["occurrence_count"] > 0


# ------------------------------------------------------------------ validators
def test_validator_detects_currency_mixing(engine):
    view = _view(engine, ["ledger_erp.sales_invoices"])
    plan = QueryPlan(source_id="finance_pg", primary_collection="ledger_erp.sales_invoices", aggregation="SUM",
                     measure=("ledger_erp.sales_invoices", "gross_total_doc"))
    sql = compile_sql(plan, view)
    res = engine.gateway.execute_query(engine.default_ctx, "finance_pg", sql)
    fs = engine.verifier.verify(engine.default_ctx, "total gross invoiced", plan, sql, res, view)
    cur = next(f for f in fs if f.validator_name == "currency_units")
    assert not cur.passed and cur.severity == "error" and "gross_total_usd" in cur.details["normalized_alternatives"]


def test_validator_detects_join_fanout(engine):
    view = _view(engine, ["ledger_erp.sales_invoices", "ledger_erp.sales_invoice_items"])
    plan = QueryPlan(source_id="finance_pg", primary_collection="ledger_erp.sales_invoices", aggregation="SUM",
                     measure=("ledger_erp.sales_invoices", "gross_total_usd"),
                     joins=[JoinSpec(left="ledger_erp.sales_invoices", left_col="invoice_id",
                                     right="ledger_erp.sales_invoice_items", right_col="invoice_id", direction="1:n")])
    sql = compile_sql(plan, view)
    res = engine.gateway.execute_query(engine.default_ctx, "finance_pg", sql)
    fs = engine.verifier.verify(engine.default_ctx, "total gross invoiced", plan, sql, res, view)
    jm = next(f for f in fs if f.validator_name == "join_multiplicity")
    assert not jm.passed and jm.severity == "error" and jm.details["evidence"]["rows"][0]["joined_rows"] > 0


def test_validator_flags_missing_time_filter_and_void_rows(engine):
    view = _view(engine, ["ledger_erp.sales_invoices"])
    plan = QueryPlan(source_id="finance_pg", primary_collection="ledger_erp.sales_invoices", aggregation="SUM",
                     measure=("ledger_erp.sales_invoices", "subtotal_amount_usd"))
    sql = compile_sql(plan, view)
    res = engine.gateway.execute_query(engine.default_ctx, "finance_pg", sql)
    fs = engine.verifier.verify(engine.default_ctx, "total invoice subtotal in USD in 2025", plan, sql, res, view)
    assert any(f.validator_name == "date_period" and not f.passed for f in fs)
    assert any(f.validator_name == "nullifying_status" and f.severity == "warning" and "VOID" in f.details["values"] for f in fs)


def test_reconciliation_validator_uses_discovered_rule(engine):
    view = _view(engine, ["ledger_erp.sales_invoices"])
    plan = QueryPlan(source_id="finance_pg", primary_collection="ledger_erp.sales_invoices", aggregation="SUM",
                     measure=("ledger_erp.sales_invoices", "open_balance_usd"),
                     filters=[FilterSpec(collection="ledger_erp.sales_invoices", column="doc_currency", op="=", value="GBP",
                                         origin="value_match", matched_text="GBP")])
    sql = compile_sql(plan, view)
    res = engine.gateway.execute_query(engine.default_ctx, "finance_pg", sql)
    fs = engine.verifier.verify(engine.default_ctx, "open balance for GBP invoices", plan, sql, res, view)
    rec = [f for f in fs if f.validator_name == "reconciliation"]
    assert rec and all(f.passed for f in rec)


# ------------------------------------------------------------------ adjudicator
def _cand(agent, value, sql="SELECT 1", measure=("c", "m")):
    plan = QueryPlan(source_id="finance_pg", primary_collection="c", aggregation="SUM", measure=measure)
    return AgentReasoningCandidate(agent_name=agent, interpreted_question="x", query_or_plan=sql, source_id="finance_pg",
                                   final_answer_candidate=str(value), structured_value=value,
                                   resolved_entities_and_metrics={"plan": plan.model_dump()})


def test_adjudicator_prefers_evidence_and_abstains_when_unclear(engine):
    err = ValidationFinding(validator_name="currency_units", passed=False, severity="error", message="mixed currencies")
    ok = ValidationFinding(validator_name="execution", passed=True, severity="info", message="ok")
    a, b = _cand("Explorer", 100.0, measure=("c", "m_doc")), _cand("Hercules", 90.0, measure=("c", "m_usd"))
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                      {"Explorer": [err], "Hercules": [ok]})
    assert d.selected_path == "Hercules" and d.agreement_status == "disagreement_resolved" and "measure" in d.disagreement_analysis
    d2 = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                       {"Explorer": [ok], "Hercules": [ok]})
    assert d2.selected_path == "Abstained_With_Uncertainty" and d2.verification_status == VerificationStatus.INFERRED_CANDIDATE
    d3 = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": _cand("Explorer", 5.0), "Hercules": _cand("Hercules", 5.0)},
                                       {"Explorer": [ok], "Hercules": [ok]})
    assert d3.agreement_status == "exact_match" and d3.verification_status == VerificationStatus.VERIFIED_FACT


# ------------------------------------------------------------------ orchestration
def test_dual_path_workflow_end_to_end(engine):
    state = engine.ask("How many invoices have settlement status OVERDUE?", use_memory=False, export_formats=["csv", "json"])
    nodes = [t["node"] for t in state["trace"]]
    assert {"explorer", "hercules", "verify", "adjudicate", "finalize"} <= set(nodes)
    assert nodes.index("retrieve_knowledge") < min(nodes.index("explorer"), nodes.index("hercules")) < nodes.index("verify")
    d = state["decision"]
    assert d["verification_status"] == "verified_fact" and d["agreement_status"] == "exact_match"
    assert state["explorer_candidate"]["structured_value"] == state["hercules_candidate"]["structured_value"]
    assert all(Path(x["path"]).exists() for x in state["exports"])
    explorer_obs = state["explorer_candidate"]["investigation_trace"]["tool_observations"]
    assert explorer_obs and all(o["objective"] for o in explorer_obs)


def test_parallel_branches_cannot_overwrite_each_other():
    """The isolation guarantee the workflow relies on: concurrent writes to one plain key are rejected."""
    from langgraph.errors import InvalidUpdateError
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        shared: str
        trace: Annotated[list, operator.add]

    g = StateGraph(S)
    g.add_node("a", lambda s: {"shared": "a"})
    g.add_node("b", lambda s: {"shared": "b"})
    g.add_edge(START, "a")
    g.add_edge(START, "b")
    g.add_edge("a", END)
    g.add_edge("b", END)
    with pytest.raises(InvalidUpdateError):
        g.compile().invoke({})


def test_memory_learns_and_respects_tenant_boundaries(engine):
    a, b = AccessContext(tenant_id="t_mem_a", user_id="u1"), AccessContext(tenant_id="t_mem_b", user_id="u1")
    for ctx in (a, b):
        engine.memory.clear(ctx)
    src = "memory_test_source"
    versions = {src: "v1"}
    engine.memory.remember(a, "query_template", src, "v1", "k1", {"sql": "SELECT 1"}, question="open balance by status")
    engine.memory.remember(a, "value_mapping", src, "v1", "k2", {"term": "overdue", "collection": "c", "column": "s",
                                                                 "value": "OVERDUE"}, shared=False)
    assert engine.memory.similar(a, "open balance by status", "query_template", versions)
    assert not engine.memory.similar(b, "open balance by status", "query_template", versions)
    assert "overdue" in engine.memory.value_mappings(a, versions)
    assert not engine.memory.value_mappings(AccessContext(tenant_id="t_mem_a", user_id="u2"), versions)
    assert engine.memory.invalidate_source(src, "v2") >= 2
    assert not engine.memory.similar(a, "open balance by status", "query_template", {src: "v2"})
    for ctx in (a, b):
        engine.memory.clear(ctx)


def test_exports_all_formats(engine, tmp_path):
    from backend.export import export_result

    rows = [{"status": "OPEN", "value": 1.5}, {"status": "PAID", "value": 0.0}]
    for fmt in ("csv", "excel", "json", "markdown"):
        p = Path(export_result(rows, fmt, str(tmp_path), "t", {"question": "q", "query": "SELECT 1"}))
        assert p.exists() and p.stat().st_size > 0
    assert pd.read_excel(next(tmp_path.glob("*.xlsx")), sheet_name="lineage").shape[0] >= 2


def test_api_endpoints(engine):
    from fastapi.testclient import TestClient

    from backend.api import app as api

    original = api.engine
    api.engine = lambda: engine  # reuse the session engine
    try:
        c = TestClient(api.app)
        assert c.get("/health").json()["status"] == "ok"
        r = c.post("/ask", json={"question": "How many agreement files have execution status Fully Signed?",
                                 "mode": "explorer", "use_memory": False})
        body = r.json()
        assert r.status_code == 200 and body["verification_status"] == "verified_fact" and body["evidence"]
        rej = c.post("/query", json={"source_id": "finance_pg", "query": "DROP TABLE ledger_erp.sales_invoices"}).json()
        assert not rej["succeeded"] and rej["error_message"].startswith("REJECTED")
        assert c.get("/sources", headers={"X-Tenant-Id": "nobody"}).json() == []
    finally:
        api.engine = original
