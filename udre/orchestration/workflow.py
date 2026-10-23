"""Dual-path LangGraph workflow.

parse_request -> resolve_sources -> retrieve_knowledge -> {explorer || hercules} -> verify
  -> adjudicate -> (targeted_followup)? -> finalize

Explorer and Hercules run in the same superstep and write disjoint state keys
(``explorer_*`` / ``hercules_*``): LangGraph rejects concurrent writes to one key without a reducer,
so neither branch can overwrite the other.  Shared input is limited to verified knowledge; no
candidate content crosses between the branches before verification.
"""

from __future__ import annotations

import operator
import time
import uuid
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import psycopg2
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from udre.agents.planner import QueryPlan, compile_sql, describe_answer, parse_question, structured_value
from udre.contracts.models import AdjudicationDecision, AgentReasoningCandidate, ValidationFinding, VerificationStatus
from udre.gateway.registry import AccessContext
from udre.skills.tools import ToolBox, ToolSession


class UDREState(TypedDict, total=False):
    request_id: str
    question: str
    tenant_id: str
    user_id: str
    mode: str
    use_memory: bool
    export_formats: List[str]
    parsed: Dict[str, Any]
    sources: List[str]
    versions: Dict[str, str]
    shared_knowledge: Dict[str, Any]
    context_stats: Dict[str, Any]
    explorer_candidate: Dict[str, Any]
    explorer_error: str
    hercules_candidate: Dict[str, Any]
    hercules_error: str
    findings: Dict[str, List[Dict[str, Any]]]
    decision: Dict[str, Any]
    followup: Dict[str, Any]
    exports: List[Dict[str, Any]]
    persisted: Dict[str, Any]
    trace: Annotated[List[Dict[str, Any]], operator.add]


TRANSIENT = RetryPolicy(max_attempts=3, initial_interval=0.2, retry_on=(psycopg2.OperationalError, ConnectionError))


def _t(node: str, t0: float, **extra: Any) -> List[Dict[str, Any]]:
    return [{"node": node, "ms": round((time.perf_counter() - t0) * 1000, 1), **extra}]


class DualPathWorkflow:
    def __init__(self, engine: "Any"):
        self.e = engine
        self._contexts: Dict[str, AccessContext] = {}
        self.checkpointer = InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    # ------------------------------------------------------------------ helpers
    def _ctx(self, state: UDREState) -> AccessContext:
        return self._contexts.get(state["request_id"]) or AccessContext(tenant_id=state["tenant_id"], user_id=state["user_id"],
                                                                        max_rows=self.e.settings.max_rows)

    def _toolbox(self, state: UDREState) -> ToolBox:
        return ToolBox(self.e.gateway, self.e.kg, self.e.memory if state.get("use_memory") else None, self.e.settings.exports_dir)

    # ------------------------------------------------------------------ nodes
    def parse_request(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        f = parse_question(state["question"])
        parsed = {"aggregation": f.aggregation, "years": f.years, "periods": f.periods, "relative_time": f.relative_time,
                  "group": f.group_phrase, "entity": f.entity_phrase, "exclusions": f.exclusions, "null_terms": f.null_terms}
        return {"parsed": parsed, "trace": _t("parse_request", t0, parsed=parsed)}

    def resolve_sources(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        sources = [s.source_id for s in self.e.gateway.sources(ctx)]
        if not sources:
            raise PermissionError("no data sources are accessible to this user")
        versions = {}
        for sid in sources:
            report = self.e.builder.build(ctx, sid)  # incremental: no-op when the schema fingerprint is unchanged
            versions[sid] = report.schema_version
            if self.e.memory and not report.skipped:
                self.e.memory.invalidate_source(sid, report.schema_version)
        return {"sources": sources, "versions": versions, "trace": _t("resolve_sources", t0, sources=sources)}

    def retrieve_knowledge(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        patterns, mappings = [], {}
        if state.get("use_memory") and self.e.memory:
            patterns = self.e.memory.similar(ctx, state["question"], "query_template", state["versions"], k=5, min_score=0.55)
            mappings = self.e.memory.value_mappings(ctx, state["versions"])
        compiled = self.e.compiled_context(ctx, use_memory=bool(state.get("use_memory")))
        stats = compiled.compression_stats.model_dump()
        shared = {"verified_patterns": [{"question": p["question"], "score": p["score"]} for p in patterns],
                  "learned_value_terms": sorted(mappings)}
        return {"shared_knowledge": shared, "context_stats": stats,
                "trace": _t("retrieve_knowledge", t0, patterns=len(patterns), context_tokens=stats["compressed_tokens"])}

    def route_agents(self, state: UDREState) -> List[str]:
        mode = state.get("mode", "dual")
        return {"explorer": ["explorer"], "hercules": ["hercules"]}.get(mode, ["explorer", "hercules"])

    def explorer(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        session = ToolSession(self._toolbox(state), self._ctx(state), "Explorer", max_calls=self.e.settings.explorer_max_steps * 3)
        try:
            cand = self.e.explorer.run(state["question"], session)
            return {"explorer_candidate": cand.model_dump(mode="json"),
                    "trace": _t("explorer", t0, tool_calls=cand.tool_calls_count, value=str(cand.structured_value)[:80])}
        except (psycopg2.OperationalError, ConnectionError):
            raise
        except Exception as exc:
            return {"explorer_error": f"{type(exc).__name__}: {exc}", "trace": _t("explorer", t0, error=str(exc)[:200])}

    def hercules(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        session = ToolSession(self._toolbox(state), ctx, "Hercules", max_calls=30)
        try:
            compiled = self.e.compiled_context(ctx, use_memory=bool(state.get("use_memory")))
            cand = self.e.hercules.run(state["question"], session, compiled)
            return {"hercules_candidate": cand.model_dump(mode="json"),
                    "trace": _t("hercules", t0, tool_calls=cand.tool_calls_count, value=str(cand.structured_value)[:80])}
        except (psycopg2.OperationalError, ConnectionError):
            raise
        except Exception as exc:
            return {"hercules_error": f"{type(exc).__name__}: {exc}", "trace": _t("hercules", t0, error=str(exc)[:200])}

    def _view_for(self, ctx: AccessContext, cand: AgentReasoningCandidate):
        from udre.agents.catalog import view_from_described

        plan = QueryPlan(**cand.resolved_entities_and_metrics["plan"])
        tb = ToolBox(self.e.gateway, self.e.kg, None, self.e.settings.exports_dir)
        colls = {plan.primary_collection} | {j.right for j in plan.joins} | {f.collection for f in plan.filters}
        described = [tb.describe_collection(ctx, plan.source_id, c) for c in sorted(colls)]
        rels = tb.relationship_graph(ctx, plan.source_id)
        adapter = self.e.gateway.adapter(ctx, plan.source_id)
        return plan, view_from_described(plan.source_id, adapter.dialect, adapter.quote, described, rels, self.e.packs)

    def verify(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        findings: Dict[str, List[Dict[str, Any]]] = {}
        for key, name in (("explorer_candidate", "Explorer"), ("hercules_candidate", "Hercules")):
            if not state.get(key):
                continue
            cand = AgentReasoningCandidate(**state[key])
            plan, view = self._view_for(ctx, cand)
            result = self.e.gateway.execute_query(ctx, plan.source_id, cand.query_or_plan)
            fs = self.e.verifier.verify(ctx, state["question"], plan, cand.query_or_plan, result, view)
            findings[name] = [f.model_dump(mode="json") for f in fs]
        summary = {n: {"errors": sum(1 for f in fs if not f["passed"] and f["severity"] == "error"),
                       "warnings": sum(1 for f in fs if f["severity"] == "warning")} for n, fs in findings.items()}
        return {"findings": findings, "trace": _t("verify", t0, summary=summary)}

    def adjudicate(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        cands = {n: AgentReasoningCandidate(**state[k]) for k, n in (("explorer_candidate", "Explorer"),
                                                                     ("hercules_candidate", "Hercules")) if state.get(k)}
        if not cands:
            errors = [state.get("explorer_error"), state.get("hercules_error")]
            decision = AdjudicationDecision(question=state["question"], selected_path="Abstained_With_Uncertainty",
                                            agreement_status="material_uncertainty", final_answer=f"No agent produced a candidate: {errors}",
                                            winning_query="", winning_source_id="", verification_status=VerificationStatus.INFERRED_CANDIDATE)
        else:
            findings = {n: [ValidationFinding(**f) for f in fs] for n, fs in state.get("findings", {}).items()}
            decision = self.e.adjudicator.adjudicate(ctx, state["question"], cands, findings)
        return {"decision": decision.model_dump(mode="json"),
                "trace": _t("adjudicate", t0, path=decision.selected_path, status=decision.agreement_status)}

    def needs_followup(self, state: UDREState) -> str:
        d = state.get("decision", {})
        if d.get("agreement_status") != "material_uncertainty" or "several definitions" in (d.get("disagreement_analysis") or ""):
            return "finalize"
        fixable = any(f["validator_name"] in ("currency_units", "join_multiplicity") and not f["passed"]
                      for fs in state.get("findings", {}).values() for f in fs)
        return "targeted_followup" if fixable else "finalize"

    def targeted_followup(self, state: UDREState) -> Dict[str, Any]:
        """Repair the candidate whose only blocking issue is a deterministic, fixable defect, then re-verify."""
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        for key, name in (("explorer_candidate", "Explorer"), ("hercules_candidate", "Hercules")):
            if not state.get(key):
                continue
            cand = AgentReasoningCandidate(**state[key])
            plan, view = self._view_for(ctx, cand)
            fixed = plan.model_copy(deep=True)
            changed = []
            for f in state["findings"].get(name, []):
                if f["validator_name"] == "currency_units" and not f["passed"] and f["details"].get("normalized_alternatives"):
                    fixed.measure = (plan.measure[0], f["details"]["normalized_alternatives"][0])
                    changed.append(f"measure -> {fixed.measure[1]} (reporting currency)")
                if f["validator_name"] == "join_multiplicity" and not f["passed"]:
                    fixed.joins = [j for j in fixed.joins if j.direction == "n:1"]
                    changed.append("removed fan-out join")
            if not changed:
                continue
            sql = compile_sql(fixed, view)
            res = self.e.gateway.execute_query(ctx, fixed.source_id, sql)
            fs = self.e.verifier.verify(ctx, state["question"], fixed, sql, res, view)
            if res.succeeded and not any(not f.passed and f.severity == "error" for f in fs):
                value = structured_value(fixed, res.rows)
                d = dict(state["decision"])
                d.update(selected_path="Followup_Resolved", agreement_status="disagreement_resolved",
                         final_answer=describe_answer(state["question"], fixed, value), final_structured_data=res.rows[:50],
                         winning_query=sql, winning_source_id=fixed.source_id, verification_status="verified_fact",
                         validation_findings=[f.model_dump(mode="json") for f in fs], confidence_score=0.8,
                         query_lineage=list(d.get("query_lineage", [])) + [f"followup({name}): {sql}"],
                         disagreement_analysis=(d.get("disagreement_analysis") or "") + f" | follow-up repaired {name}: {changed}")
                return {"decision": d, "followup": {"repaired": name, "changes": changed},
                        "trace": _t("targeted_followup", t0, repaired=name)}
        return {"followup": {"repaired": None}, "trace": _t("targeted_followup", t0, repaired=None)}

    def finalize(self, state: UDREState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        d = state["decision"]
        persisted = {"query_template": 0, "value_mapping": 0, "failed_query": 0}
        if state.get("use_memory") and self.e.memory and d.get("winning_query"):
            sid = d["winning_source_id"]
            version = state["versions"].get(sid, "unknown")
            if d.get("verification_status") == "verified_fact" and d.get("selected_path") != "Abstained_With_Uncertainty":
                self.e.memory.remember(ctx, "query_template", sid, version, d["winning_query"],
                                       {"sql": d["winning_query"], "answer": d["final_answer"]}, question=state["question"],
                                       evidence_refs=[e["evidence_id"] for e in d.get("supporting_evidence", [])[:5]])
                persisted["query_template"] += 1
                winner = next((state[k] for k in ("explorer_candidate", "hercules_candidate")
                               if state.get(k) and state[k]["query_or_plan"] == d["winning_query"]), None)
                for f in (winner or {}).get("applied_filters", []):
                    if f["origin"] == "value_match" and f.get("matched_text"):
                        self.e.memory.remember(ctx, "value_mapping", sid, version, f"{f['matched_text']}->{f['collection']}.{f['column']}",
                                               {"term": f["matched_text"], "collection": f["collection"],
                                                "column": f["column"], "value": f["value"]}, question=f["matched_text"])
                        persisted["value_mapping"] += 1
            for k in ("explorer_candidate", "hercules_candidate"):
                for obs in (state.get(k) or {}).get("investigation_trace", {}).get("tool_observations", []):
                    if obs["tool_name"] == "execute_query" and obs["observation_summary"].startswith("error"):
                        self.e.memory.remember(ctx, "failed_query", sid, version, obs["raw_output_preview"][:200],
                                               {"error": obs["observation_summary"]}, question=state["question"],
                                               status="failed")
                        persisted["failed_query"] += 1
        exports = []
        for fmt in state.get("export_formats") or []:
            meta = {"title": state["question"], "question": state["question"], "answer": d["final_answer"],
                    "verification_status": d["verification_status"], "agreement_status": d["agreement_status"],
                    "query": d["winning_query"], "evidence": [e["observed_summary"] for e in d.get("supporting_evidence", [])[:10]],
                    "assumptions": d.get("unresolved_assumptions", [])}
            exports.append(ToolBox(self.e.gateway, self.e.kg, None, self.e.settings.exports_dir).export_results(
                d.get("final_structured_data") or [], fmt, name=state["question"][:40], metadata=meta))
        return {"persisted": persisted, "exports": exports, "trace": _t("finalize", t0, persisted=persisted)}

    # ------------------------------------------------------------------ graph
    def _build(self) -> StateGraph:
        g = StateGraph(UDREState)
        g.add_node("parse_request", self.parse_request)
        g.add_node("resolve_sources", self.resolve_sources, retry_policy=TRANSIENT)
        g.add_node("retrieve_knowledge", self.retrieve_knowledge, retry_policy=TRANSIENT)
        g.add_node("explorer", self.explorer, retry_policy=TRANSIENT)
        g.add_node("hercules", self.hercules, retry_policy=TRANSIENT)
        g.add_node("verify", self.verify, retry_policy=TRANSIENT)
        g.add_node("adjudicate", self.adjudicate)
        g.add_node("targeted_followup", self.targeted_followup, retry_policy=TRANSIENT)
        g.add_node("finalize", self.finalize, retry_policy=TRANSIENT)
        g.add_edge(START, "parse_request")
        g.add_edge("parse_request", "resolve_sources")
        g.add_edge("resolve_sources", "retrieve_knowledge")
        g.add_conditional_edges("retrieve_knowledge", self.route_agents, ["explorer", "hercules"])
        g.add_edge("explorer", "verify")
        g.add_edge("hercules", "verify")
        g.add_edge("verify", "adjudicate")
        g.add_conditional_edges("adjudicate", self.needs_followup, ["targeted_followup", "finalize"])
        g.add_edge("targeted_followup", "finalize")
        g.add_edge("finalize", END)
        return g

    def run(self, question: str, ctx: AccessContext, mode: str = "dual", use_memory: bool = True,
            export_formats: Optional[List[str]] = None) -> Dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        self._contexts[request_id] = ctx
        t0 = time.perf_counter()
        try:
            state = self.graph.invoke({"request_id": request_id, "question": question, "tenant_id": ctx.tenant_id,
                                       "user_id": ctx.user_id, "mode": mode, "use_memory": use_memory,
                                       "export_formats": export_formats or [], "trace": []},
                                      config={"configurable": {"thread_id": request_id}})
        finally:
            self._contexts.pop(request_id, None)
        state["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return state
