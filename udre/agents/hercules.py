"""Hercules: independent reasoning from comprehensive compressed context, then live verification.

Hercules starts from the compiled semantic context of the whole accessible environment (schemas,
ontology aliases, metric definitions, value dictionaries, relationships, verified query patterns),
interprets the question on its own, and then uses the same tools against real data to resolve
values missing from the dictionary and to check its plan.  It never receives Explorer's state.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Sequence

from udre.llm.base import LLMUnavailable

from udre.agents.catalog import view_from_context
from udre.agents.common import build_candidate, evidence_from_result, execute_with_repair
from udre.agents.planner import PlannerWeights, SemanticPlanner, _lit, parse_question
from udre.compiler.semantic_compiler import SemanticContextCompiler
from udre.contracts.models import (
    AgentReasoningCandidate,
    CompiledSemanticContext,
    ExplorerInvestigationState,
    ValidationFinding,
)
from udre.ontology.embedding import cosine, embed
from udre.skills.tools import ToolSession

HERCULES_WEIGHTS = PlannerWeights(name=2.0, description=0.0, collection=1.0, concept=1.2, metric_synonym=1.5,
                                  unit=0.4, learned=1.5, precision=1.0, anchor=4.0)


class HerculesAgent:
    name = "Hercules"

    def __init__(self, packs: Sequence[Dict[str, Any]], compiler: SemanticContextCompiler, context_budget: int = 32000,
                 llm=None, max_turns: int = 20):
        self.packs = packs
        self.compiler = compiler
        self.context_budget = context_budget
        self.llm = llm
        self.max_turns = max_turns

    def run(self, question: str, session: ToolSession, compiled: CompiledSemanticContext, on_event=None) -> AgentReasoningCandidate:
        if self.llm is None:
            return self.run_deterministic(question, session, compiled)
        try:
            return asyncio.run(self.arun_llm(question, session, compiled, on_event))
        except LLMUnavailable as exc:
            fresh = ToolSession(session.toolbox, session.ctx, session.agent, max_calls=session.max_calls)
            cand = self.run_deterministic(question, fresh, compiled)
            cand.reasoning_mode, cand.failure = "deterministic_fallback", f"LLM unavailable: {exc}"
            return cand

    async def arun_llm(self, question: str, session: ToolSession, compiled: CompiledSemanticContext,
                       on_event=None) -> AgentReasoningCandidate:
        import json

        from udre.agents.llm_agent import TOOL_SPECS, LLMInvestigation, candidate_from_submission, serialize_result
        from udre.agents.prompts import HERCULES_SYSTEM, TRUST_RULES

        t0 = time.perf_counter()
        text, info = self.compiler.render(compiled, question, self.context_budget)
        inclusion = {k: info.get(k) for k in ("full_tokens", "rendered_tokens", "collections_total", "collections_included",
                                              "samples_included")}
        if info.get("collections_included", 0) < info.get("collections_total", 0):
            inclusion["omitted_collections_note"] = ("Only the most relevant collections fit the budget; omitted ones exist and "
                                                     "can be found with discover_collections / list_collection_contexts.")
        user = (f"Question: {question}\n\nInclusion report: {json.dumps(inclusion)}\n\nCompressed overview of authorized data:\n"
                + serialize_result("global_context", {"overview": json.loads(text)})
                + "\n\nForm your own interpretation and plan first, then verify with tools and submit.")
        loop = LLMInvestigation(self.llm, self.name, HERCULES_SYSTEM.format(trust=TRUST_RULES), session,
                                max_turns=self.max_turns, on_event=on_event)
        sub, final = await loop.run(user, list(TOOL_SPECS))
        sid = sub.source_id if sub else ""
        colls = session.toolbox.gateway.collections(session.ctx, sid) if sid else []
        dialect = session.toolbox.gateway.adapter(session.ctx, sid).dialect if sid else None
        cand = candidate_from_submission(self.name, question, sub, final, session, loop.usage,
                                         (time.perf_counter() - t0) * 1000, colls, dialect, context_tokens=info["rendered_tokens"])
        if cand.investigation_trace is not None:
            cand.investigation_trace.known_facts.insert(0, f"context inclusion: {json.dumps(inclusion)}")
        return cand

    def run_deterministic(self, question: str, session: ToolSession, compiled: CompiledSemanticContext) -> AgentReasoningCandidate:
        t0 = time.perf_counter()
        st = ExplorerInvestigationState(user_requirements=[question])
        _text, render_info = self.compiler.render(compiled, question, self.context_budget)
        context_tokens = render_info["rendered_tokens"]
        st.known_facts.append(f"compressed context: {context_tokens} tokens, "
                              f"{render_info['collections_included']}/{render_info['collections_total']} collections")
        frame = parse_question(question)
        evidence = []
        # verified pattern reuse only for near-identical questions; otherwise patterns are just context
        qv = embed(question)
        for p in compiled.verified_query_patterns:
            if p.get("q") and cosine(qv, embed(p["q"])) >= 0.985:
                st.known_facts.append(f"near-identical verified pattern found: {p['q']}")
        best = None
        for sid in compiled.sources:
            src = next((s for s in session.call("discover_sources", objective="confirm accessible sources and dialects",
                                                phase="CONTEXT") if s["source_id"] == sid), None)
            if not src:
                continue
            quote = (lambda n: ".".join('"' + part + '"' for part in n.split(".")))
            view = view_from_context(compiled, sid, src["dialect"], quote, self.packs)
            if not view.columns:
                continue
            learned = session.call("lookup_knowledge", objective="fetch learned value mappings", phase="CONTEXT",
                                   query=question).get("learned_value_mappings", {})
            view.learned_values = {t: [m for m in maps if m.get("source_id") == sid] for t, maps in learned.items()}
            planner = SemanticPlanner(view, HERCULES_WEIGHTS)
            planner.external_matches = self._live_values(session, sid, question, view)
            try:
                plan = planner.plan(question)
            except ValueError:
                continue
            score = (plan.candidates[0]["score"] if plan.candidates and plan.measure else 5.0) + 2.0 * len(
                [f for f in plan.filters if f.origin in ("value_match", "exclusion")])
            st.hypotheses.append(f"{sid}: {plan.rationale}")
            if best is None or score > best[0]:
                best = (score, plan, view, planner)
        if best is None:
            raise RuntimeError("Hercules found no viable plan")
        _score, plan, view, planner = best
        sample = session.call("sample_rows", objective=f"inspect real rows of {plan.primary_collection}", phase="INSPECT",
                              source_id=plan.source_id, collection=plan.primary_collection, n=3)
        evidence.append(evidence_from_result(self.name, plan.source_id, f"sample {plan.primary_collection}", sample, "row_sample",
                                             plan.primary_collection))
        plan, sql, res = execute_with_repair(session, planner, plan, view, "", st)
        evidence.append(evidence_from_result(self.name, plan.source_id, sql, res, collection=plan.primary_collection))
        checks = self._verify(session, plan, view, res, evidence, st)
        st.tool_observations = session.observations
        st.evidence_references = evidence
        return build_candidate(self.name, question, plan, sql, res, session, evidence, checks, st,
                               (time.perf_counter() - t0) * 1000, context_tokens=context_tokens)

    def _live_values(self, session: ToolSession, sid: str, question: str, view) -> List[Dict[str, Any]]:
        """Look up literal phrases the value dictionary does not already contain (dictionary holds top values only)."""
        from udre.agents.explorer import ExplorerAgent
        from udre.gateway.base import normalize_text

        known = {normalize_text(v) for c in view.columns.values() for v in c.top_values}
        out: List[Dict[str, Any]] = []
        for phrase in ExplorerAgent._literal_phrases(question)[:3]:
            if normalize_text(phrase) in known:
                continue
            found = session.call("search_column_values", objective=f"value '{phrase}' absent from dictionary; check data",
                                 phase="INSPECT", source_id=sid, term=phrase, k=3)
            out += [m for m in found if m["similarity_score"] >= 0.85]
        return out

    def _verify(self, session: ToolSession, plan, view, res, evidence, st) -> List[ValidationFinding]:
        if not res["succeeded"]:
            return [ValidationFinding(validator_name="hercules_execution", passed=False, severity="error",
                                      message=res.get("error_message") or "failed")]
        q = view.quote
        checks = []
        conds = [f"{q(f.column)} = {_lit(f.value)}" for f in plan.filters
                 if f.collection == plan.primary_collection and f.origin in ("value_match", "learned_mapping")]
        sql = f"SELECT COUNT(*) AS n FROM {q(plan.primary_collection)}" + (f" WHERE {' AND '.join(conds)}" if conds else "")
        r = session.call("execute_query", objective="count rows in scope of the primary-collection filters", phase="VERIFY",
                         source_id=plan.source_id, query=sql)
        n = r["rows"][0]["n"] if r["succeeded"] else 0
        evidence.append(evidence_from_result(self.name, plan.source_id, sql, r, collection=plan.primary_collection))
        checks.append(ValidationFinding(validator_name="hercules_scope_rows", passed=n > 0, severity="info" if n else "error",
                                        message=f"{n} rows fall inside the filtered scope"))
        st.validation_outcomes.append(f"scope rows: {n}")
        return checks
