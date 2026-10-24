"""Dual-path LangGraph workflow.

parse_request -> understand_intent -> respond -> finalize                      (direct reply or clarifying question)
                                   -> resolve_sources -> retrieve_knowledge -> {explorer || hercules} -> verify
                                      -> adjudicate -> (targeted_followup)? -> finalize          (data questions)

Explorer and Hercules run in the same superstep and write disjoint state keys
(``explorer_*`` / ``hercules_*``): LangGraph rejects concurrent writes to one key without a reducer,
so neither branch can overwrite the other.  Shared input is limited to verified knowledge; no
candidate content crosses between the branches before verification.
"""

from __future__ import annotations

import operator
import threading
import time
import uuid
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import psycopg2
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from backend.agents.planner import QueryPlan, compile_sql, describe_answer, parse_question, structured_value
from backend.contracts.models import AdjudicationDecision, AgentReasoningCandidate, ValidationFinding, VerificationStatus
from backend.gateway.registry import AccessContext
from backend.skills.tools import ToolBox, ToolSession
from backend.verification.validators import candidate_completeness


class BackendState(TypedDict, total=False):
    request_id: str
    question: str
    tenant_id: str
    user_id: str
    mode: str
    use_memory: bool
    export_formats: List[str]
    parsed: Dict[str, Any]
    history: List[Dict[str, str]]
    original_question: str
    intent: Dict[str, Any]
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
    manifest: Dict[str, Any]
    consistency: Dict[str, Any]
    answer_id: str
    artifacts: List[Dict[str, Any]]
    detail: Dict[str, Any]
    trace: Annotated[List[Dict[str, Any]], operator.add]


TRANSIENT = RetryPolicy(max_attempts=3, initial_interval=0.2, retry_on=(psycopg2.OperationalError, ConnectionError))


def _t(node: str, t0: float, **extra: Any) -> List[Dict[str, Any]]:
    return [{"node": node, "ms": round((time.perf_counter() - t0) * 1000, 1), **extra}]


class DualPathWorkflow:
    def __init__(self, engine: "Any"):
        self.e = engine
        self._contexts: Dict[str, AccessContext] = {}
        self._sinks: Dict[str, Any] = {}
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._ev_lock = threading.Lock()
        self._leases: Dict[str, Any] = {}
        self.checkpointer = InMemorySaver()
        self.graph = self._build().compile(checkpointer=self.checkpointer)

    def _wrap(self, name: str, fn):
        """Emit real node start/finish events (with the node's own trace data) around every graph node."""
        def node(state: BackendState) -> Dict[str, Any]:
            emit = self._sink(state["request_id"])
            emit({"event": "node", "node": name, "status": "started", "ts": round(time.time(), 3)})
            try:
                out = fn(state)
            except Exception as exc:
                emit({"event": "node", "node": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"[:400]})
                raise
            detail = dict((out.get("trace") or [{}])[-1]) if isinstance(out, dict) else {}
            for reserved in ("node", "event", "ts"):
                detail.pop(reserved, None)
            if "status" in detail:  # a node's own status (e.g. agreement status) must not hide that the node finished
                detail["outcome"] = detail.pop("status")
            emit({**detail, "event": "node", "node": name, "status": "finished", "ts": round(time.time(), 3)})
            return out
        return node

    def _sink(self, request_id: str):
        def emit(event: Dict[str, Any]) -> None:
            with self._ev_lock:
                self._events.setdefault(request_id, []).append(event)
            cb = self._sinks.get(request_id)
            if cb:
                try:
                    cb(event)
                except Exception:
                    pass
        return emit

    # ------------------------------------------------------------------ helpers
    def _ctx(self, state: BackendState) -> AccessContext:
        return self._contexts.get(state["request_id"]) or AccessContext(tenant_id=state["tenant_id"], user_id=state["user_id"],
                                                                        max_rows=self.e.settings.max_rows)

    def _toolbox(self, state: BackendState) -> ToolBox:
        return self.e.toolbox(use_memory=bool(state.get("use_memory")))

    @staticmethod
    def _brief(state: BackendState) -> Optional[Dict[str, Any]]:
        it = state.get("intent") or {}
        return it.get("brief") if it.get("mode") == "llm" else None  # the rule-based fallback has nothing to add

    # ------------------------------------------------------------------ nodes
    def parse_request(self, state: BackendState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        f = parse_question(state["question"])
        parsed = {"aggregation": f.aggregation, "years": f.years, "periods": f.periods, "relative_time": f.relative_time,
                  "group": f.group_phrase, "entity": f.entity_phrase, "exclusions": f.exclusions, "null_terms": f.null_terms}
        return {"parsed": parsed, "trace": _t("parse_request", t0, parsed=parsed)}

    def understand_intent(self, state: BackendState) -> Dict[str, Any]:
        """Answer directly, ask a clarifying question, or brief the investigating agents."""
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        emit = self._sink(state["request_id"])
        if self.e.intent is None or state.get("mode", "dual") != "dual":
            intent = {"route": "investigate", "reason": "intent step not used for single-agent runs", "mode": "skipped"}
            return {"intent": intent, "trace": _t("understand_intent", t0, route="investigate", mode="skipped")}
        session = ToolSession(self._toolbox(state), ctx, "Intent", max_calls=10, run_id=state["request_id"], on_event=emit)
        versions = {v["source_id"]: v for v in (self.e.runtime_store.source_versions(ctx.tenant_id) if self.e.runtime_store else [])}
        freshness = [{"source_id": s.source_id, "name": s.name, "sync_status": versions.get(s.source_id, {}).get("sync_status", "no events yet"),
                      "last_sync_at": str(versions.get(s.source_id, {}).get("last_sync_at") or "") or None}
                     for s in self.e.gateway.sources(ctx)]
        decision, meta = self.e.intent.decide(state["question"], session, freshness, on_event=emit,
                                              history=state.get("history") or [], files=ctx.manifest.get("workspace_files") or [])
        intent = {**decision.model_dump(mode="json"), **meta, "tool_calls": len(session.observations)}
        emit({"agent": "Intent", "event": "decision", "route": decision.route, "reason": decision.reason, "mode": meta.get("mode"),
              "standalone_question": decision.standalone_question})
        out: Dict[str, Any] = {"intent": intent, "trace": _t("understand_intent", t0, route=decision.route, mode=meta.get("mode"),
                                                             tool_calls=len(session.observations))}
        standalone = (decision.standalone_question or "").strip()
        if decision.route == "investigate" and standalone and standalone != state["question"]:
            # the agents and validators work on a question that makes sense without the conversation
            out.update(question=standalone, original_question=state["question"])
        return out

    def after_intent(self, state: BackendState) -> str:
        route = state.get("intent", {}).get("route", "investigate")
        return {"investigate": "resolve_sources", "leakage": "leakage_scan"}.get(route, "respond")

    def leakage_scan(self, state: BackendState) -> Dict[str, Any]:
        """Run the leakage hunter on the conversation's uploaded files, or on the connected procurement data."""
        from backend.leakage.service import ACTION_TEXT, TYPE_LABEL

        t0 = time.perf_counter()
        ctx = self._ctx(state)
        emit = self._sink(state["request_id"])
        tenant, _, ws_id = ctx.tenant_id.partition("::ws::")
        if ws_id:
            principal = AccessContext(tenant_id=ctx.principal_tenant or tenant, user_id=ctx.user_id, max_rows=ctx.max_rows)
            res = self.e.leakage.scan_workspace(principal, ws_id, on_event=emit)
            cases, report, scope = res["cases"], res["report"], f"the {res['files']} uploaded files"
            source = "uploaded files"
        else:
            self.e.leakage.scan(ctx.tenant_id)
            rows = [r for r in self.e.leakage.cases(ctx.tenant_id) if r["status"] != "resolved"]
            cases = [{"type": r["leakage_type"], "ref": r["record_id"], "related": r.get("related_records") or [], "supplier_name": r["supplier_name"],
                      "recoverable_usd": r["recoverable_usd"], "confidence": r["confidence"], "priority_score": r["priority_score"],
                      "root_cause": r["root_cause"], "action": r["recovery_action"]} for r in rows]
            report, scope, source = {}, "the connected procurement data", "finance_pg"
        total = round(sum(c["recoverable_usd"] for c in cases), 2)
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for c in cases:
            by_type.setdefault(c["type"], []).append(c)
        lines = [f"**I found {len(cases)} likely leaks worth ${total:,.2f} recoverable** in {scope}." if cases
                 else f"**I found no spend leakage** in {scope}."]
        for t, cs in sorted(by_type.items(), key=lambda kv: -sum(c["recoverable_usd"] for c in kv[1])):
            lines.append(f"- {TYPE_LABEL.get(t, t)}: {len(cs)} case{'s' if len(cs) != 1 else ''}, "
                         f"${sum(c['recoverable_usd'] for c in cs):,.2f}")
        if cases:
            lines.append("\n**Biggest items**")
            for c in cases[:5]:
                lines.append(f"- {c['ref']} ({c.get('supplier_name') or 'unknown vendor'}), ${c['recoverable_usd']:,.2f}: {c['root_cause']}")
        if report.get("files"):
            read = [f for f in report["files"] if f["role"] != "ignore"]
            ignored = [f["table"] for f in report["files"] if f["role"] == "ignore"]
            roles: Dict[str, List[str]] = {}
            for f in read:
                roles.setdefault(f["role"].replace("_", " "), []).append(f["table"])
            lines.append("\n**What I read**")
            lines.append("- " + "; ".join(f"{r}: {', '.join(ts)}" for r, ts in roles.items()))
            counts = report.get("counts", {})
            lines.append(f"- {counts.get('supplier_invoices', 0)} invoices, {counts.get('supplier_payments', 0)} linked payments, "
                         f"{counts.get('saas_subscriptions', 0)} licences")
            if ignored:
                lines.append(f"- Not used (not accounts-payable data): {', '.join(ignored)}")
            for w in report.get("warnings", [])[:5]:
                lines.append(f"- {w}")
        rows_out = [{"leak_type": TYPE_LABEL.get(c["type"], c["type"]), "record": c["ref"], "supplier": c.get("supplier_name"),
                     "recoverable_usd": c["recoverable_usd"], "confidence": c["confidence"],
                     "recommended_action": ACTION_TEXT.get(c["action"], (c["action"], c["action"]))[1], "root_cause": c["root_cause"]}
                    for c in cases]
        decision = AdjudicationDecision(question=state["question"], selected_path="Leakage_Scan", agreement_status="not_applicable",
                                        final_answer="\n".join(lines), final_structured_data=rows_out, winning_query="",
                                        winning_source_id=source, verification_status=VerificationStatus.VERIFIED_FACT,
                                        confidence_score=0.9 if cases else 0.8, unresolved_assumptions=list(report.get("warnings", [])))
        return {"decision": decision.model_dump(mode="json"),
                "trace": _t("leakage_scan", t0, cases=len(cases), recoverable_usd=total, scope=source)}

    def respond(self, state: BackendState) -> Dict[str, Any]:
        """Turn a direct reply or clarifying question into the same decision shape the investigation produces."""
        t0 = time.perf_counter()
        it = state["intent"]
        if it["route"] == "clarify":
            text = it["clarifying_question"] + ("".join(f"\n- {o}" for o in it.get("options") or []))
            path, status, notes = "Clarification_Requested", VerificationStatus.NOT_APPLICABLE, []
        else:
            text = it["answer"]
            queried = bool(it.get("queried"))
            path = "Direct_Response"
            status = VerificationStatus.INFERRED_CANDIDATE if queried else VerificationStatus.NOT_APPLICABLE
            notes = ["answered with a single query by the intent agent; not dual-verified"] if queried else []
        decision = AdjudicationDecision(question=state["question"], selected_path=path, agreement_status="not_applicable",
                                        final_answer=text, winning_query="", winning_source_id="", verification_status=status,
                                        unresolved_assumptions=notes)
        return {"decision": decision.model_dump(mode="json"), "trace": _t("respond", t0, path=path)}

    def resolve_sources(self, state: BackendState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        sources = [s.source_id for s in self.e.gateway.sources(ctx)]
        if not sources:
            raise PermissionError("no data sources are accessible to this user")
        versions = {}
        for sid in sources:
            # incremental: ontology build and reference generation are no-ops when nothing changed
            prep = self.e.prepare_source(ctx, sid)
            versions[sid] = prep["ontology"]["schema_version"]
        from backend.runtime.versions import SnapshotLeases, resolve_manifest

        manifest = resolve_manifest(self.e, ctx, sources)
        if ctx.manifest.get("workspace_files"):
            manifest["workspace_files"] = ctx.manifest["workspace_files"]
        leases = SnapshotLeases()
        leases.open(self.e, ctx, manifest)  # both agents share this context, so they read the same snapshot
        self._leases[state["request_id"]] = leases
        ctx.manifest = manifest
        self._sink(state["request_id"])({"event": "manifest", "manifest": manifest})
        return {"sources": sources, "versions": versions, "manifest": manifest,
                "trace": _t("resolve_sources", t0, sources=sources, manifest_digest=manifest["digest"],
                            consistency={k: v["consistency"] for k, v in manifest["sources"].items()})}

    def retrieve_knowledge(self, state: BackendState) -> Dict[str, Any]:
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

    def route_agents(self, state: BackendState) -> List[str]:
        mode = state.get("mode", "dual")
        return {"explorer": ["explorer"], "hercules": ["hercules"]}.get(mode, ["explorer", "hercules"])

    def explorer(self, state: BackendState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        emit = self._sink(state["request_id"])
        session = ToolSession(self._toolbox(state), self._ctx(state), "Explorer", max_calls=self.e.settings.explorer_max_steps * 3,
                              run_id=state["request_id"], on_event=emit)
        try:
            emit({"agent": "Explorer", "event": "started", "mode": "llm" if self.e.explorer.llm else "deterministic_offline"})
            cand = self.e.explorer.run(state["question"], session, on_event=emit, brief=self._brief(state))
            emit({"agent": "Explorer", "event": "candidate", "value": str(cand.structured_value)[:200], "mode": cand.reasoning_mode})
            return {"explorer_candidate": cand.model_dump(mode="json"),
                    "trace": _t("explorer", t0, tool_calls=cand.tool_calls_count, value=str(cand.structured_value)[:80],
                                mode=cand.reasoning_mode)}
        except (psycopg2.OperationalError, ConnectionError):
            raise
        except Exception as exc:
            return {"explorer_error": f"{type(exc).__name__}: {exc}", "trace": _t("explorer", t0, error=str(exc)[:200])}

    def hercules(self, state: BackendState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        emit = self._sink(state["request_id"])
        session = ToolSession(self._toolbox(state), ctx, "Hercules", max_calls=30, run_id=state["request_id"], on_event=emit)
        try:
            compiled = self.e.compiled_context(ctx, use_memory=bool(state.get("use_memory")))
            emit({"agent": "Hercules", "event": "started", "mode": "llm" if self.e.hercules.llm else "deterministic_offline"})
            cand = self.e.hercules.run(state["question"], session, compiled, on_event=emit, brief=self._brief(state))
            emit({"agent": "Hercules", "event": "candidate", "value": str(cand.structured_value)[:200], "mode": cand.reasoning_mode})
            return {"hercules_candidate": cand.model_dump(mode="json"),
                    "trace": _t("hercules", t0, tool_calls=cand.tool_calls_count, value=str(cand.structured_value)[:80],
                                mode=cand.reasoning_mode)}
        except (psycopg2.OperationalError, ConnectionError):
            raise
        except Exception as exc:
            return {"hercules_error": f"{type(exc).__name__}: {exc}", "trace": _t("hercules", t0, error=str(exc)[:200])}

    def _view_for(self, ctx: AccessContext, cand: AgentReasoningCandidate):
        from backend.agents.catalog import view_from_described

        plan = QueryPlan(**cand.resolved_entities_and_metrics["plan"])
        tb = ToolBox(self.e.gateway, self.e.kg, None, self.e.settings.exports_dir)
        colls = {plan.primary_collection} | {j.right for j in plan.joins} | {f.collection for f in plan.filters}
        described = [tb.describe_collection(ctx, plan.source_id, c) for c in sorted(colls)]
        rels = tb.relationship_graph(ctx, plan.source_id)
        adapter = self.e.gateway.adapter(ctx, plan.source_id)
        return plan, view_from_described(plan.source_id, adapter.dialect, adapter.quote, described, rels, self.e.packs)

    def verify(self, state: BackendState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        findings: Dict[str, List[Dict[str, Any]]] = {}
        for key, name in (("explorer_candidate", "Explorer"), ("hercules_candidate", "Hercules")):
            if not state.get(key):
                continue
            cand = AgentReasoningCandidate(**state[key])
            if not cand.query_or_plan or not cand.source_id:
                findings[name] = [ValidationFinding(validator_name="execution", passed=False, severity="error",
                                                    message=f"no executable candidate ({cand.failure})").model_dump(mode="json")]
                continue
            try:
                plan, view = self._view_for(ctx, cand)
            except Exception as exc:
                findings[name] = [ValidationFinding(validator_name="source_binding", passed=False, severity="error",
                                                    message=f"plan could not be bound to the source: {exc}").model_dump(mode="json")]
                continue
            result = self.e.gateway.execute_query(ctx, plan.source_id, cand.query_or_plan)
            fs = self.e.verifier.verify(ctx, state["question"], plan, cand.query_or_plan, result, view)
            fs.append(candidate_completeness(cand))
            findings[name] = [f.model_dump(mode="json") for f in fs]
        summary = {n: {"errors": sum(1 for f in fs if not f["passed"] and f["severity"] == "error"),
                       "warnings": sum(1 for f in fs if f["severity"] == "warning")} for n, fs in findings.items()}
        return {"findings": findings, "trace": _t("verify", t0, summary=summary)}

    def adjudicate(self, state: BackendState) -> Dict[str, Any]:
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

    def llm_adjudicate(self, state: BackendState) -> Dict[str, Any]:
        """GPT adjudication over executed evidence, with at most one targeted verification query (guards in code)."""
        t0 = time.perf_counter()
        ctx = self._ctx(state)
        cands = {n: AgentReasoningCandidate(**state[k]) for k, n in (("explorer_candidate", "Explorer"),
                                                                     ("hercules_candidate", "Hercules")) if state.get(k)}
        det = AdjudicationDecision(**state["decision"])
        if not cands or self.e.llm_adjudicator is None:
            return {"trace": _t("llm_adjudicate", t0, skipped="not configured" if self.e.llm_adjudicator is None else "no candidates")}
        from backend.runtime.versions import check_consistency

        findings = {n: [ValidationFinding(**f) for f in fs] for n, fs in state.get("findings", {}).items()}
        consistency = check_consistency(self.e, ctx, state.get("manifest") or {})
        try:
            d = self.e.llm_adjudicator.adjudicate(ctx, state["question"], cands, findings, det, consistency, state.get("manifest") or {},
                                                  on_event=self._sink(state["request_id"]))
        except Exception as exc:  # LLM unavailable: deterministic decision stands, and says so
            d = det.model_copy(update={"llm_verdict": {"error": f"{type(exc).__name__}: {str(exc)[:200]}",
                                                       "fallback": "deterministic adjudication"}})
        return {"decision": d.model_dump(mode="json"),
                "trace": _t("llm_adjudicate", t0, path=d.selected_path, status=d.agreement_status,
                            verdict=(d.llm_verdict or {}).get("status"), overridden=(d.llm_verdict or {}).get("overridden"),
                            verification_actions=len(d.verification_actions))}

    def after_adjudicate(self, state: BackendState) -> str:
        return "llm_adjudicate" if self.e.llm_adjudicator is not None else self.needs_followup(state)

    def needs_followup(self, state: BackendState) -> str:
        d = state.get("decision", {})
        if d.get("agreement_status") != "material_uncertainty" or "several definitions" in (d.get("disagreement_analysis") or ""):
            return "finalize"
        fixable = any(f["validator_name"] in ("currency_units", "join_multiplicity") and not f["passed"]
                      for fs in state.get("findings", {}).values() for f in fs)
        return "targeted_followup" if fixable else "finalize"

    def targeted_followup(self, state: BackendState) -> Dict[str, Any]:
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

    def finalize(self, state: BackendState) -> Dict[str, Any]:
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
        direct = (state.get("intent") or {}).get("route", "investigate") in ("answer", "clarify")
        for fmt in [] if direct else state.get("export_formats") or []:
            meta = {"title": state["question"], "question": state["question"], "answer": d["final_answer"],
                    "verification_status": d["verification_status"], "agreement_status": d["agreement_status"],
                    "query": d["winning_query"], "evidence": [e["observed_summary"] for e in d.get("supporting_evidence", [])[:10]],
                    "assumptions": d.get("unresolved_assumptions", [])}
            exports.append(ToolBox(self.e.gateway, self.e.kg, None, self.e.settings.exports_dir).export_results(
                d.get("final_structured_data") or [], fmt, name=state["question"][:40], metadata=meta))
        # source-version consistency, snapshot release, sandbox teardown
        from pathlib import Path

        from backend.gateway.sql_guard import analyze
        from backend.runtime.versions import check_consistency

        detail = None if direct else self._detail(ctx, d)
        consistency = check_consistency(self.e, ctx, state.get("manifest") or {})
        leases = self._leases.pop(state["request_id"], None)
        if leases:
            leases.close()
        if getattr(self.e, "sandbox", None):
            self.e.sandbox.close_run(state["request_id"])
        if consistency["warnings"]:
            d = dict(d, unresolved_assumptions=list(d.get("unresolved_assumptions", [])) + consistency["warnings"])
        artifacts = []
        if getattr(self.e, "files", None):
            for x in exports:
                rec = self.e.files.save_bytes(ctx.owner_tenant, ctx.user_id, Path(x["path"]).name, Path(x["path"]).read_bytes(),
                                              kind="artifact", run_id=state["request_id"], origin=f"export:{x['format']}")
                artifacts.append(rec.public())
            artifacts += [r.public() for r in self.e.files.list(ctx.owner_tenant, ctx.user_id, kind="artifact", run_id=state["request_id"])
                          if r.origin and r.origin.startswith("sandbox:")]
        answer_id = None
        if getattr(self.e, "runtime_store", None) and d.get("winning_query"):
            try:
                dialect = self.e.gateway.adapter(ctx, d["winning_source_id"]).dialect
                tables = sorted(analyze(d["winning_query"], dialect).tables)
            except Exception:
                tables = []
            answer_id = self.e.runtime_store.save_answer(ctx.owner_tenant, state["question"], state.get("manifest") or {},
                                                         [d["winning_source_id"]], tables,
                                                         {k: d.get(k) for k in ("final_answer", "verification_status", "selected_path",
                                                                                "winning_query", "agreement_status")})
        out = {"persisted": persisted, "exports": exports, "consistency": consistency, "artifacts": artifacts,
               "trace": _t("finalize", t0, persisted=persisted, consistency=consistency["status"], artifacts=len(artifacts))}
        if detail:
            out["detail"] = detail
        if answer_id:
            out["answer_id"] = answer_id
        if consistency["warnings"]:
            out["decision"] = d
        return out

    def _detail(self, ctx: AccessContext, d: Dict[str, Any], limit: int = 50) -> Optional[Dict[str, Any]]:
        """For a single-number answer over filtered rows (e.g. COUNT/SUM ... WHERE ...), fetch the rows behind it.

        The aggregate query is rewritten to select the primary table's rows with the same FROM/JOIN/WHERE, and run
        read-only through the gateway (same pinned snapshot). Grouped or multi-row answers already carry their data.
        """
        import sqlglot
        from sqlglot import exp

        rows = d.get("final_structured_data") or []
        if not d.get("winning_query") or len(rows) != 1 or len(rows[0]) != 1:
            return None
        try:
            dialect = self.e.gateway.adapter(ctx, d["winning_source_id"]).dialect
            tree = sqlglot.parse_one(d["winning_query"], read=dialect)
            if not isinstance(tree, exp.Select) or tree.args.get("group") or not tree.find(exp.AggFunc):
                return None
            source = tree.args.get("from_") or tree.args.get("from")  # the key name differs across sqlglot versions
            table = source.this if source is not None else None
            if not isinstance(table, exp.Table):
                return None
            star = exp.Star() if not tree.args.get("joins") else exp.Column(this=exp.Star(), table=exp.to_identifier(table.alias_or_name))
            q = tree.copy()
            for key in ("order", "limit", "having", "offset"):
                q.set(key, None)
            q.set("expressions", [star])
            sql = q.limit(limit).sql(dialect=dialect)
            res = self.e.gateway.execute_query(ctx, d["winning_source_id"], sql, max_rows=limit)
        except Exception:
            return None
        if not res.succeeded or not res.rows:
            return None
        return {"query": sql, "columns": res.columns, "rows": res.rows[:limit], "row_count": len(res.rows), "limited": len(res.rows) >= limit}

    # ------------------------------------------------------------------ graph
    def _build(self) -> StateGraph:
        g = StateGraph(BackendState)
        w = self._wrap
        g.add_node("parse_request", w("parse_request", self.parse_request))
        g.add_node("understand_intent", w("understand_intent", self.understand_intent), retry_policy=TRANSIENT)
        g.add_node("respond", w("respond", self.respond))
        g.add_node("leakage_scan", w("leakage_scan", self.leakage_scan), retry_policy=TRANSIENT)
        g.add_node("resolve_sources", w("resolve_sources", self.resolve_sources), retry_policy=TRANSIENT)
        g.add_node("retrieve_knowledge", w("retrieve_knowledge", self.retrieve_knowledge), retry_policy=TRANSIENT)
        g.add_node("explorer", w("explorer", self.explorer), retry_policy=TRANSIENT)
        g.add_node("hercules", w("hercules", self.hercules), retry_policy=TRANSIENT)
        g.add_node("verify", w("verify", self.verify), retry_policy=TRANSIENT)
        g.add_node("adjudicate", w("adjudicate", self.adjudicate))
        g.add_node("llm_adjudicate", w("llm_adjudicate", self.llm_adjudicate))
        g.add_node("targeted_followup", w("targeted_followup", self.targeted_followup), retry_policy=TRANSIENT)
        g.add_node("finalize", w("finalize", self.finalize), retry_policy=TRANSIENT)
        g.add_edge(START, "parse_request")
        g.add_edge("parse_request", "understand_intent")
        g.add_conditional_edges("understand_intent", self.after_intent, ["resolve_sources", "respond", "leakage_scan"])
        g.add_edge("respond", "finalize")
        g.add_edge("leakage_scan", "finalize")
        g.add_edge("resolve_sources", "retrieve_knowledge")
        g.add_conditional_edges("retrieve_knowledge", self.route_agents, ["explorer", "hercules"])
        g.add_edge("explorer", "verify")
        g.add_edge("hercules", "verify")
        g.add_edge("verify", "adjudicate")
        g.add_conditional_edges("adjudicate", self.after_adjudicate, ["llm_adjudicate", "targeted_followup", "finalize"])
        g.add_conditional_edges("llm_adjudicate", self.needs_followup, ["targeted_followup", "finalize"])
        g.add_edge("targeted_followup", "finalize")
        g.add_edge("finalize", END)
        return g

    def run(self, question: str, ctx: AccessContext, mode: str = "dual", use_memory: bool = True,
            export_formats: Optional[List[str]] = None, on_event=None, history: Optional[List[Dict[str, str]]] = None,
            _retry: int = 1) -> Dict[str, Any]:
        import copy
        import os

        request_id = uuid.uuid4().hex[:12]
        run_ctx = copy.copy(ctx)  # per-run manifest and snapshots never leak into the caller's context
        run_ctx.snapshots, run_ctx.manifest = {}, dict(ctx.manifest or {})
        self._contexts[request_id] = run_ctx
        if on_event:
            self._sinks[request_id] = on_event
        t0 = time.perf_counter()
        try:
            state = self.graph.invoke({"request_id": request_id, "question": question, "tenant_id": ctx.tenant_id,
                                       "user_id": ctx.user_id, "mode": mode, "use_memory": use_memory,
                                       "export_formats": export_formats or [], "history": history or [], "trace": []},
                                      config={"configurable": {"thread_id": request_id}})
        finally:
            self._contexts.pop(request_id, None)
            self._sinks.pop(request_id, None)
            leases = self._leases.pop(request_id, None)
            if leases:
                leases.close()
            with self._ev_lock:
                events = self._events.pop(request_id, [])
        state["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        state["events"] = events
        state["request_id"] = request_id
        if (state.get("consistency") or {}).get("status") == "changed_during_run" and _retry > 0 and \
                os.environ.get("BACKEND_RETRY_ON_VERSION_CHANGE", "1") == "1":
            if on_event:
                on_event({"event": "retry", "reason": "source version changed during the analysis without a consistent snapshot",
                          "moved_sources": state["consistency"]["moved_sources"]})
            retried = self.run(question, ctx, mode, use_memory, export_formats, on_event, history, _retry=_retry - 1)
            retried["retried_from"] = request_id
            return retried
        return state
