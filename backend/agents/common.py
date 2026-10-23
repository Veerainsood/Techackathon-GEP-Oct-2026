"""Helpers shared by both agents: execution with repair, evidence records, candidate assembly."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.agents.planner import CatalogView, QueryPlan, compile_sql, describe_answer, structured_value
from backend.contracts.models import AgentReasoningCandidate, EvidenceReference, ExplorerInvestigationState, ValidationFinding
from backend.skills.tools import ToolSession


def evidence_from_result(agent: str, source_id: str, sql: str, res: Dict[str, Any], kind: str = "query_result",
                         collection: Optional[str] = None) -> EvidenceReference:
    summary = (f"{res.get('row_count')} rows; first={str(res.get('rows', [])[:1])[:200]}" if res.get("succeeded")
               else f"error: {res.get('error_message')}")
    return EvidenceReference(evidence_id="ev:" + hashlib.sha1(f"{agent}|{source_id}|{sql}".encode()).hexdigest()[:16],
                             source_id=source_id, collection_name=collection, evidence_type=kind, query_or_action=sql,
                             observed_summary=summary, sample_data=(res.get("rows") or [])[:5])


def interpret(plan: QueryPlan) -> str:
    parts = [f"{plan.aggregation}"]
    parts.append(f"of {plan.measure[0]}.{plan.measure[1]}" if plan.measure else f"of rows in {plan.primary_collection}")
    if plan.group_by:
        parts.append("grouped by " + ", ".join(f"{c}.{k}" for c, k in plan.group_by))
    if plan.filters:
        parts.append("where " + " AND ".join(f"{f.column} {f.op} {f.value}" if f.op != "IS NULL" else f"{f.column} IS NULL"
                                             for f in plan.filters))
    if plan.limit:
        parts.append(f"top {plan.limit}")
    return " ".join(parts)


def execute_with_repair(session: ToolSession, planner, plan: QueryPlan, view: CatalogView, phase_prefix: str,
                        state: ExplorerInvestigationState, max_repairs: int = 2) -> Tuple[QueryPlan, str, Dict[str, Any]]:
    """Execute the plan; on failure apply deterministic repairs driven by the error message."""
    sql = compile_sql(plan, view)
    state.candidate_queries.append(sql)
    res = session.call("execute_query", objective="answer the question with the current plan", phase=f"{phase_prefix}QUERY",
                       source_id=plan.source_id, query=sql)
    attempt = 0
    while not res["succeeded"] and attempt < max_repairs:
        attempt += 1
        err = res.get("error_message") or ""
        state.validation_outcomes.append(f"execution failed: {err[:160]}")
        repaired = plan.model_copy(deep=True)
        bad_col = re.search(r'column "?([\w.]+)"? does not exist|Referenced column "?(\w+)"? not found', err, re.I)
        if bad_col:
            name = (bad_col.group(1) or bad_col.group(2)).split(".")[-1]
            repaired.filters = [f for f in repaired.filters if f.column != name]
            repaired.group_by = [g for g in repaired.group_by if g[1] != name]
            if repaired.measure and repaired.measure[1] == name and len(plan.candidates) > 1:
                coll, col = plan.candidates[1]["column"].rsplit(".", 1)
                repaired.measure, repaired.primary_collection = (coll, col), coll
        elif re.search(r"extract|date_part|timestamp|type", err, re.I):
            for f in repaired.filters:
                if f.op == "YEAR":
                    f.op, f.value = "LIKE", f"{f.value}%"
        else:
            repaired.filters = [f for f in repaired.filters if f.origin != "learned_mapping"]
            repaired.joins = repaired.joins[:-1] if repaired.joins else repaired.joins
        plan = repaired
        plan.rationale.append(f"repair #{attempt} after error: {err[:100]}")
        sql = compile_sql(plan, view)
        state.candidate_queries.append(sql)
        res = session.call("execute_query", objective=f"retry after repair #{attempt}", phase=f"{phase_prefix}REPAIR",
                           source_id=plan.source_id, query=sql)
    return plan, sql, res


def build_candidate(agent: str, question: str, plan: QueryPlan, sql: str, res: Dict[str, Any], session: ToolSession,
                    evidence: List[EvidenceReference], checks: List[ValidationFinding], state: ExplorerInvestigationState,
                    latency_ms: float, context_tokens: int = 0) -> AgentReasoningCandidate:
    value = structured_value(plan, res.get("rows") or []) if res.get("succeeded") else None
    open_q = [a["detail"] + (f": {a.get('options')}" if a.get("options") else "") for a in plan.ambiguities] + plan.assumptions
    state.remaining_ambiguities = open_q
    answer = describe_answer(question, plan, value) if res.get("succeeded") else f"Could not answer: {res.get('error_message')}"
    return AgentReasoningCandidate(
        agent_name=agent, interpreted_question=interpret(plan),
        resolved_entities_and_metrics={"plan": plan.model_dump(), "measure": plan.measure, "primary": plan.primary_collection,
                                       "aggregation": plan.aggregation, "group_by": plan.group_by},
        applied_filters=[f.model_dump() for f in plan.filters],
        source_mappings=[{"term": f.matched_text, "maps_to": f"{f.collection}.{f.column}", "value": str(f.value),
                          "origin": f.origin} for f in plan.filters],
        query_or_plan=sql, source_id=plan.source_id, observed_results=(res.get("rows") or [])[:50],
        evidence_references=evidence, verification_results=checks, open_questions=open_q,
        final_answer_candidate=answer, structured_value=value, tool_calls_count=len(session.observations),
        tokens_used=session.tokens_observed + context_tokens, latency_ms=round(latency_ms, 1), investigation_trace=state,
        result_provenance=res.get("provenance") or {})
