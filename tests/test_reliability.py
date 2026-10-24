"""Phase 2 reliability: question coverage, candidate completeness, evidence-driven disagreement resolution."""

import pytest

from backend.agents.planner import QueryPlan
from backend.contracts.models import AgentReasoningCandidate, ValidationFinding, VerificationStatus
from backend.verification.validators import candidate_completeness


@pytest.fixture(scope="module")
def engine(pg_uri, uploads_dir):
    from backend.engine import build_engine

    return build_engine(llm_providers=(None, None))


def _cand(agent, value, sql="SELECT 1", ambiguities=None):
    plan = QueryPlan(source_id="finance_pg", primary_collection="c", aggregation="SUM", measure=("c", agent.lower()),
                     ambiguities=ambiguities or [])
    return AgentReasoningCandidate(agent_name=agent, interpreted_question="x", query_or_plan=sql, source_id="finance_pg",
                                   final_answer_candidate=str(value), structured_value=value, observed_results=[{"value": value}],
                                   resolved_entities_and_metrics={"plan": plan.model_dump()})


def _cov(unbound, ratio=0.5):
    return ValidationFinding(validator_name="question_coverage", passed=True, severity="info", message="cov",
                             details={"ratio": ratio, "uncovered_physical": unbound, "uncovered": []})


def test_unbound_question_terms_resolve_disagreement_in_either_direction(engine):
    a, b = _cand("Explorer", 1.0), _cand("Hercules", 2.0)
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                      {"Explorer": [_cov(["arr", "weighted"], 0.6)], "Hercules": [_cov([], 1.0)]})
    assert d.selected_path == "Hercules" and "does not bind question terms ['arr', 'weighted']" in d.disagreement_analysis
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                      {"Explorer": [_cov([], 1.0)], "Hercules": [_cov(["region", "stage"], 0.6)]})
    assert d.selected_path == "Explorer"


def test_no_resolution_when_both_plans_leave_different_terms_unbound(engine):
    a, b = _cand("Explorer", 1.0), _cand("Hercules", 2.0)
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                      {"Explorer": [_cov(["arr", "weighted"])], "Hercules": [_cov(["quota"])]})
    assert d.selected_path == "Abstained_With_Uncertainty" and d.verification_status == VerificationStatus.INFERRED_CANDIDATE


def test_blocking_error_prevents_coverage_win(engine):
    a, b = _cand("Explorer", 1.0), _cand("Hercules", 2.0)
    err = ValidationFinding(validator_name="currency_units", passed=False, severity="error", message="mixed currencies")
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b},
                                      {"Explorer": [_cov(["arr", "weighted"])], "Hercules": [_cov([]), err]})
    assert d.selected_path != "Hercules" or d.verification_status != VerificationStatus.VERIFIED_FACT


def test_agent_abstention_flag_forces_explicit_uncertainty(engine):
    a = _cand("Explorer", 5.0, ambiguities=[{"type": "agent_abstained", "detail": "revenue could be invoiced or recognized"}])
    b = _cand("Hercules", 5.0)
    d = engine.adjudicator.adjudicate(engine.default_ctx, "q", {"Explorer": a, "Hercules": b}, {"Explorer": [], "Hercules": []})
    assert d.agreement_status == "material_uncertainty" and "invoiced or recognized" in d.disagreement_analysis


def test_candidate_completeness():
    ok = _cand("Explorer", 1.0)
    ok.evidence_references = []
    f = candidate_completeness(ok)
    assert f.severity == "warning" and "evidence" in f.details["missing"]
    empty = AgentReasoningCandidate(agent_name="Explorer", interpreted_question="", query_or_plan="", source_id="",
                                    final_answer_candidate="none", resolved_entities_and_metrics={})
    f = candidate_completeness(empty)
    assert f.severity == "error" and {"executed_query", "source_lineage", "result_rows"} <= set(f.details["missing"])


def test_coverage_validator_flags_unbound_measure_terms(engine):
    from backend.agents.catalog import view_from_described
    from backend.skills.tools import ToolBox

    ctx = engine.default_ctx
    tb = ToolBox(engine.gateway, engine.kg, None, engine.settings.exports_dir)
    colls = ["pipeline_crm.quota_carriers", "pipeline_crm.deal_opportunities"]
    adapter = engine.gateway.adapter(ctx, "finance_pg")
    view = view_from_described("finance_pg", adapter.dialect, adapter.quote, [tb.describe_collection(ctx, "finance_pg", c) for c in colls],
                               tb.relationship_graph(ctx, "finance_pg"), engine.packs)
    wrong = QueryPlan(source_id="finance_pg", primary_collection="pipeline_crm.quota_carriers", aggregation="TOP",
                      measure=("pipeline_crm.quota_carriers", "annual_quota_usd"), group_by=[("pipeline_crm.quota_carriers", "rep_full_name")])
    f = engine.verifier.question_coverage(ctx, "top 3 quota carriers by total weighted ARR in USD of their deals", wrong, view, None)[0]
    assert {"weighted", "deal"} <= set(f.details["uncovered_physical"])
