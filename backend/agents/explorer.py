"""Explorer: focused, iterative, tool-driven investigation with no pre-loaded global context.

Loop: DISCOVER -> IDENTIFY KNOWLEDGE GAPS -> INSPECT -> QUERY -> OBSERVE -> VERIFY -> ANSWER.
All schema knowledge Explorer uses is obtained through tool calls during this run; filters are
resolved from live value searches against the source.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from backend.llm.base import LLMUnavailable

from backend.agents.catalog import view_from_described
from backend.agents.common import build_candidate, evidence_from_result, execute_with_repair
from backend.agents.planner import PlannerWeights, SemanticPlanner, _lit, parse_question
from backend.contracts.models import AgentReasoningCandidate, ExplorerInvestigationState, ValidationFinding
from backend.gateway.base import normalize_text
from backend.skills.tools import ToolBudgetExceeded, ToolSession

EXPLORER_WEIGHTS = PlannerWeights(name=2.0, description=0.8, collection=1.4, concept=1.0, metric_synonym=0.0,
                                  unit=0.4, learned=1.5, precision=1.0, anchor=4.0)


class ExplorerAgent:
    name = "Explorer"

    def __init__(self, packs: Sequence[Dict[str, Any]], max_steps: int = 14, inspect_k: int = 5, llm=None, max_turns: int = 24,
                 max_sources: int = 2, reserve_calls: int = 10):
        self.packs = packs
        self.max_steps = max_steps
        self.inspect_k = inspect_k
        self.max_sources = max_sources
        self.reserve_calls = reserve_calls
        self.llm = llm  # LLMProvider or None (offline deterministic mode)
        self.max_turns = max_turns

    def run(self, question: str, session: ToolSession, on_event=None) -> AgentReasoningCandidate:
        """LLM tool-calling investigation when a provider is configured; otherwise the deterministic planner."""
        if on_event and session.on_event is None:
            session.on_event = on_event
        if self.llm is None:
            return self.run_deterministic(question, session)
        try:
            return asyncio.run(self.arun_llm(question, session, on_event))
        except LLMUnavailable as exc:
            fresh = ToolSession(session.toolbox, session.ctx, session.agent, max_calls=session.max_calls, run_id=session.run_id,
                                on_event=session.on_event)
            cand = self.run_deterministic(question, fresh)
            cand.reasoning_mode, cand.failure = "deterministic_fallback", f"LLM unavailable: {exc}"
            return cand

    async def arun_llm(self, question: str, session: ToolSession, on_event=None) -> AgentReasoningCandidate:
        import json as _json

        from backend.agents.llm_agent import LLMInvestigation, available_tools, candidate_from_submission, serialize_result
        from backend.agents.prompts import EXPLORER_SYSTEM, TRUST_RULES

        t0 = time.perf_counter()
        skill = session.call("load_skill", objective="load the investigation procedure", phase="INIT", skill_id="database-investigation")
        catalog = session.call("collection_catalog", objective="lightweight catalog of authorized collections", phase="INIT")
        knowledge = session.call("lookup_knowledge", objective="reuse verified knowledge", phase="INIT", query=question)
        lines = [f"- {c['source_id']}:{c['collection']} | rows={c.get('rows')} | {c.get('summary', '')[:120]}"
                 + (f" | metrics={','.join(c.get('metrics', [])[:5])}" if c.get("metrics") else "")
                 + (f" | mode={c.get('provenance_mode')}" if c.get("provenance_mode") else "") for c in catalog]
        manifest = session.ctx.manifest or {}
        user = (f"Question: {question}\n\nData-version manifest (both agents use these versions):\n"
                + serialize_result("manifest", {k: manifest.get(k) for k in ("sources", "workspace_files", "digest")})
                + "\n\nLightweight collection catalog (load details progressively):\n"
                + serialize_result("collection_catalog", {"collections": "\n".join(lines)}) + "\n\nVerified knowledge:\n"
                + serialize_result("lookup_knowledge", knowledge)
                + "\n\nInvestigate with tools, keep explicit state, and submit a grounded candidate.")
        system = EXPLORER_SYSTEM.format(skill=skill["instructions"], trust=TRUST_RULES)
        loop = LLMInvestigation(self.llm, self.name, system, session, max_turns=self.max_turns, on_event=on_event)
        sub, final = await loop.run(user, available_tools(session))
        sid = sub.source_id if sub else ""
        colls = session.toolbox.gateway.collections(session.ctx, sid) if sid else []
        dialect = session.toolbox.gateway.adapter(session.ctx, sid).dialect if sid else None
        return candidate_from_submission(self.name, question, sub, final, session, loop.usage,
                                         (time.perf_counter() - t0) * 1000, colls, dialect)

    def run_deterministic(self, question: str, session: ToolSession) -> AgentReasoningCandidate:
        t0 = time.perf_counter()
        st = ExplorerInvestigationState(user_requirements=[question])
        evidence = []
        frame = parse_question(question)
        # ---------------- DISCOVER
        sources = session.call("discover_sources", objective="find which sources this user can query", phase="DISCOVER")
        st.known_facts.append(f"accessible sources: {[s['source_id'] for s in sources]}")
        knowledge = session.call("lookup_knowledge", objective="reuse verified knowledge and learned value mappings",
                                 phase="DISCOVER", query=question)
        learned = knowledge.get("learned_value_mappings", {})
        if knowledge.get("verified_query_patterns"):
            st.known_facts.append(f"{len(knowledge['verified_query_patterns'])} similar verified query patterns exist")
        # ---------------- IDENTIFY KNOWLEDGE GAPS
        st.unknown_facts = ["which collection holds the measure/entity", "physical column for each business term",
                            "real values for each filter phrase"]
        if frame.years or frame.periods or frame.relative_time:
            st.unknown_facts.append("which date/period column carries the requested time")
        if frame.group_phrase or frame.entity_phrase:
            st.unknown_facts.append(f"column representing '{frame.group_phrase or frame.entity_phrase}'")
        literals = self._literal_phrases(question)
        st.hypotheses.append(f"aggregation {frame.aggregation}; literal value phrases {literals}")

        # rank sources by retrieval relevance; investigate the most relevant ones while keeping budget in reserve
        ranked = session.call("discover_collections", objective="rank sources by relevance to the question", phase="DISCOVER",
                              query=question, k=10)
        order = list(dict.fromkeys([h["source_id"] for h in ranked] + [s["source_id"] for s in sources]))
        st.known_facts.append(f"source relevance order: {order}")
        best = None
        for sid in order[: self.max_sources]:
            if session.max_calls - len(session.observations) < self.reserve_calls:
                st.remaining_ambiguities.append(f"skipped source {sid}: tool budget reserved for query and verification")
                break
            try:
                cand = self._investigate_source(question, sid, session, st, learned, literals)
            except ToolBudgetExceeded as exc:
                st.validation_outcomes.append(str(exc))
                break
            if cand and (best is None or cand["score"] > best["score"]):
                best = cand
        if best is None:
            raise RuntimeError("Explorer found no viable plan in any source")
        st.known_facts.append(f"selected source {best['plan'].source_id} (score {best['score']:.2f})")
        # ---------------- QUERY / OBSERVE (with repair)
        plan, sql, res = execute_with_repair(session, best["planner"], best["plan"], best["view"], "", st)
        evidence.append(evidence_from_result(self.name, plan.source_id, sql, res, collection=plan.primary_collection))
        st.tool_observations = session.observations
        st.known_facts.append("main query " + ("succeeded" if res["succeeded"] else "failed"))
        # ---------------- VERIFY (independent checks the agent runs itself)
        try:
            checks = self._verify(session, plan, best["view"], res, evidence, st)
        except ToolBudgetExceeded as exc:
            checks = [ValidationFinding(validator_name="explorer_budget", passed=False, severity="warning",
                                        message=f"self-verification incomplete: {exc}")]
        st.evidence_references = evidence
        return build_candidate(self.name, question, plan, sql, res, session, evidence, checks, st,
                               (time.perf_counter() - t0) * 1000)

    # .................................................................. investigation per source
    def _investigate_source(self, question, sid, session: ToolSession, st, learned, literals) -> Optional[Dict[str, Any]]:
        skills = session.call("discover_collections", objective="retrieve collections relevant to the question",
                              phase="DISCOVER", query=question, source_id=sid, k=self.inspect_k)
        if not skills:
            return None
        names = [s["collection"] for s in skills]
        st.known_facts.append(f"{sid}: candidate collections {names}")
        matches: List[Dict[str, Any]] = []
        for phrase in literals[:4]:
            found = session.call("search_column_values", objective=f"resolve filter phrase '{phrase}' to a real value",
                                 phase="INSPECT", source_id=sid, term=phrase, k=5)
            strong = [m for m in found if m["similarity_score"] >= 0.85]
            matches += strong
            if strong:
                st.known_facts.append(f"'{phrase}' found as {strong[0]['collection_name']}.{strong[0]['column_name']}="
                                      f"{strong[0]['matched_value']} ({strong[0]['occurrence_count']} rows)")
            else:
                st.remaining_ambiguities.append(f"no exact value found for '{phrase}' in {sid}")
        for m in matches:
            if m["collection_name"] not in names:
                names.append(m["collection_name"])
        described = []
        rels: List[Dict[str, Any]] = []
        for coll in names[: self.inspect_k + 3]:
            described.append(session.call("describe_collection", objective=f"inspect schema and profiles of {coll}",
                                          phase="INSPECT", source_id=sid, collection=coll))
        rel_graph = session.call("relationship_graph", objective="learn verified join paths", phase="INSPECT", source_id=sid)
        # pull in parents reachable from inspected collections (needed for labels / filters on parents)
        have = {d["collection"] for d in described}
        for r in rel_graph:
            if r["child"] in have and r["parent"] not in have and len(described) < self.inspect_k + 6:
                described.append(session.call("describe_collection", objective=f"inspect referenced parent {r['parent']}",
                                              phase="INSPECT", source_id=sid, collection=r["parent"]))
                have.add(r["parent"])
        rels = [r for r in rel_graph if r["child"] in have and r["parent"] in have]
        adapter_quote = (lambda n: ".".join('"' + p + '"' for p in n.split(".")))
        dialect = next(s["dialect"] for s in session.call("discover_sources", objective="confirm dialect", phase="INSPECT")
                       if s["source_id"] == sid)
        view = view_from_described(sid, dialect, adapter_quote, described, rels, self.packs)
        view.learned_values = {t: [m for m in maps if m.get("source_id") == sid] for t, maps in learned.items()}
        planner = SemanticPlanner(view, EXPLORER_WEIGHTS)
        planner.external_matches = matches
        try:
            plan = planner.plan(question)
        except ValueError as exc:
            st.remaining_ambiguities.append(f"{sid}: {exc}")
            return None
        score = (plan.candidates[0]["score"] if plan.candidates and plan.measure else 5.0) + 2.0 * len(
            [f for f in plan.filters if f.origin in ("value_match", "exclusion")])
        st.hypotheses.append(f"{sid}: {plan.rationale}")
        return {"plan": plan, "view": view, "planner": planner, "score": score}

    @staticmethod
    def _literal_phrases(question: str) -> List[str]:
        """Phrases likely to be data values: CODE_LIKE tokens, ALLCAPS words, Title Case runs, quoted text."""
        out = re.findall(r"'([^']+)'|\"([^\"]+)\"", question)
        phrases = [a or b for a, b in out]
        phrases += re.findall(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b", question)
        phrases += [w for w in re.findall(r"\b[A-Z]{3,}\b", question)]
        for run in re.findall(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+)+\b", question):
            if not run.split()[0] in ("What", "How", "Which", "Total"):
                phrases.append(run)
        for single in re.findall(r"(?<!^)(?<![.?!]\s)\b[A-Z][a-z]{3,}\b", question):
            if single not in " ".join(phrases) and single not in ("What", "How", "Which", "Total", "USD"):
                phrases.append(single)
        seen, uniq = set(), []
        for p in phrases:
            if normalize_text(p) not in seen:
                seen.add(normalize_text(p))
                uniq.append(p)
        return uniq

    # .................................................................. verification
    def _verify(self, session: ToolSession, plan, view, res, evidence, st) -> List[ValidationFinding]:
        checks: List[ValidationFinding] = []
        if not res["succeeded"]:
            return [ValidationFinding(validator_name="explorer_execution", passed=False, severity="error",
                                      message=res.get("error_message") or "failed")]
        q = view.quote
        for f in plan.filters:
            if f.origin in ("value_match", "exclusion"):
                sql = f"SELECT COUNT(*) AS n FROM {q(f.collection)} WHERE {q(f.column)} = {_lit(f.value)}"
                r = session.call("execute_query", objective=f"confirm filter value '{f.value}' exists", phase="VERIFY",
                                 source_id=plan.source_id, query=sql)
                n = r["rows"][0]["n"] if r["succeeded"] else 0
                evidence.append(evidence_from_result(self.name, plan.source_id, sql, r, collection=f.collection))
                checks.append(ValidationFinding(validator_name="explorer_filter_presence", passed=n > 0,
                                                severity="info" if n else "error",
                                                message=f"{f.column}='{f.value}' present in {n} rows"))
                st.validation_outcomes.append(f"filter {f.column}={f.value}: {n} rows")
        if plan.measure and plan.aggregation in ("SUM", "TOP") and not plan.group_by:
            pk = [c.column for c in view.cols_of(plan.primary_collection) if c.is_pk]
            if pk and plan.joins:
                base = res["rows"][0].get("value") if res["rows"] else None
                inner = re.sub(r"^SELECT .*? FROM ", f"SELECT DISTINCT t0.{q(pk[0])} AS k, t0.{q(plan.measure[1])} AS m FROM ",
                               res["query"], count=1)
                inner = re.sub(r" LIMIT \d+$", "", inner)
                sql = f"SELECT SUM(m) AS value FROM ({inner}) d"
                r = session.call("execute_query", objective="independent recomputation at primary-key grain", phase="VERIFY",
                                 source_id=plan.source_id, query=sql)
                alt = r["rows"][0]["value"] if r["succeeded"] and r["rows"] else None
                same = alt is not None and base is not None and abs(float(alt) - float(base)) < 0.01
                evidence.append(evidence_from_result(self.name, plan.source_id, sql, r))
                checks.append(ValidationFinding(validator_name="explorer_grain_recompute", passed=same,
                                                severity="info" if same else "error",
                                                message=f"key-grain recomputation {alt} vs main {base}"))
                st.validation_outcomes.append(f"grain recompute {'matches' if same else 'differs'}")
        return checks
