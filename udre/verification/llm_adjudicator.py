"""LLM evidence adjudicator (configured for GPT-5.6 Sol on Microsoft Foundry) on top of deterministic adjudication.

The model receives only executed evidence: both candidates' SQL, real result rows, evidence records, deterministic
validation findings, the deterministic decision and the source-version consistency status.  Its output is a
schema-validated verdict.  Guards enforced in code, regardless of the verdict:
  * a selected candidate must have an executed result and no blocking (error) finding
  * question-level ambiguity flagged deterministically is never overridden into a definitive answer
  * the final number is always the selected candidate's own executed result (never averaged or rewritten)
  * at most one verification query, read-only through the gateway, against a source in the manifest
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from udre.agents.llm_agent import serialize_result
from udre.agents.prompts import ADJUDICATOR_SYSTEM
from udre.contracts.models import AdjudicationDecision, AgentReasoningCandidate, ValidationFinding, VerificationStatus
from udre.llm.base import LLMProvider, LLMUnavailable, ToolSpec, Usage


class VerificationAction(BaseModel):
    source_id: str
    sql: str = Field(description="One read-only SELECT")
    purpose: str
    discriminates: str = Field(description="What result would favour which candidate")


class AdjudicatorVerdict(BaseModel):
    status: Literal["selected", "needs_verification", "unresolved"]
    selected_agent: Optional[Literal["Explorer", "Hercules"]] = None
    disagreement: Optional[str] = None
    evidence_ids: List[str] = Field(default_factory=list)
    verification_action: Optional[VerificationAction] = None
    rationale: str = Field(max_length=1500)


SUBMIT = ToolSpec("submit_verdict", "Submit the structured adjudication verdict.", AdjudicatorVerdict.model_json_schema())


def _blocking(cand: AgentReasoningCandidate, findings: List[ValidationFinding]) -> List[str]:
    return [f"{f.validator_name}: {f.message}" for f in list(findings) + list(cand.verification_results)
            if not f.passed and f.severity == "error"]


class LLMAdjudicator:
    def __init__(self, provider: LLMProvider, gateway: Any):
        self.provider = provider
        self.gateway = gateway

    def _payload(self, question: str, cands: Dict[str, AgentReasoningCandidate], findings: Dict[str, List[ValidationFinding]],
                 det: AdjudicationDecision, consistency: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"question": question, "deterministic_decision": {
            "selected_path": det.selected_path, "agreement_status": det.agreement_status,
            "analysis": (det.disagreement_analysis or "")[:1500]}, "consistency": consistency,
            "sources": {k: {kk: v.get(kk) for kk in ("data_version", "schema_version", "mode", "consistency")}
                        for k, v in (manifest.get("sources") or {}).items()}, "candidates": {}}
        for name, c in cands.items():
            out["candidates"][name] = {
                "reasoning_mode": c.reasoning_mode, "interpretation": c.interpreted_question, "source_id": c.source_id,
                "sql": c.query_or_plan, "structured_value": c.structured_value, "result_rows": c.observed_results[:20],
                "open_questions": c.open_questions[:8],
                "evidence": [{"id": e.evidence_id, "type": e.evidence_type, "action": e.query_or_action[:240],
                              "observed": e.observed_summary[:240]} for e in c.evidence_references[:15]],
                "findings": [{"validator": f.validator_name, "passed": f.passed, "severity": f.severity, "message": f.message[:240]}
                             for f in findings.get(name, []) + list(c.verification_results)],
                "blocking_errors": _blocking(c, findings.get(name, [])),
            }
        return out

    async def _ask(self, messages: List[Dict[str, Any]], usage: Usage) -> AdjudicatorVerdict:
        for _attempt in range(2):
            resp = await self.provider.acomplete(ADJUDICATOR_SYSTEM, messages, [SUBMIT], tool_choice="submit_verdict",
                                                 max_tokens=2000, usage=usage)
            messages.append({"role": "assistant", "content": resp.raw_content or [{"type": "text", "text": resp.text or ""}]})
            call = next((c for c in resp.tool_calls if c.name == "submit_verdict"), None)
            if call is None:
                messages.append({"role": "user", "content": [{"type": "text", "text": "Call submit_verdict."}]})
                continue
            try:
                return AdjudicatorVerdict.model_validate(call.arguments)
            except ValidationError as exc:
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                                              "content": f"Schema validation failed: {exc.errors()[:5]}"}]})
        raise LLMUnavailable("adjudicator did not return a valid verdict")

    def adjudicate(self, ctx: Any, question: str, cands: Dict[str, AgentReasoningCandidate],
                   findings: Dict[str, List[ValidationFinding]], det: AdjudicationDecision, consistency: Dict[str, Any],
                   manifest: Dict[str, Any], on_event=None) -> AdjudicationDecision:
        emit = on_event or (lambda e: None)
        usage = Usage()
        payload = self._payload(question, cands, findings, det, consistency, manifest)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text":
                                           "Adjudicate using only this evidence:\n" + serialize_result("evidence", payload)}]}]
        verdict = asyncio.run(self._ask(messages, usage))
        emit({"agent": "Adjudicator", "event": "verdict", "status": verdict.status, "selected": verdict.selected_agent,
              "rationale": verdict.rationale[:600]})
        actions: List[Dict[str, Any]] = []
        if verdict.status == "needs_verification" and verdict.verification_action:
            a = verdict.verification_action
            allowed = set((manifest.get("sources") or {})) | {c.source_id for c in cands.values()}
            if a.source_id not in allowed:
                result = {"succeeded": False, "error_message": f"source {a.source_id} is not part of this analysis"}
            else:
                result = self.gateway.execute_query(ctx, a.source_id, a.sql, max_rows=50).model_dump()
            actions.append({"source_id": a.source_id, "sql": a.sql, "purpose": a.purpose, "discriminates": a.discriminates,
                            "succeeded": result.get("succeeded"), "rows": (result.get("rows") or [])[:20],
                            "error": result.get("error_message")})
            emit({"agent": "Adjudicator", "event": "verification", "sql": a.sql, "source_id": a.source_id, "purpose": a.purpose,
                  "rows": (result.get("rows") or [])[:8], "columns": result.get("columns"), "error": result.get("error_message")})
            messages.append({"role": "user", "content": [{"type": "text", "text":
                             "Result of your verification query:\n" + serialize_result("verification", result) +
                             "\nNow submit a final verdict with status 'selected' or 'unresolved'."}]})
            verdict = asyncio.run(self._ask(messages, usage))
            if verdict.status == "needs_verification":
                verdict = verdict.model_copy(update={"status": "unresolved", "rationale": verdict.rationale +
                                                     " [system: only one verification action is allowed]"})
            emit({"agent": "Adjudicator", "event": "verdict", "status": verdict.status, "selected": verdict.selected_agent,
                  "rationale": verdict.rationale[:600], "final": True})
        return self._apply(verdict, cands, findings, det, actions, usage)

    def _apply(self, v: AdjudicatorVerdict, cands, findings, det: AdjudicationDecision, actions, usage: Usage) -> AdjudicationDecision:
        record = {**v.model_dump(), "model": self.provider.model, "usage": usage.as_dict(), "overridden": None}
        question_ambiguous = "several definitions" in (det.disagreement_analysis or "")
        d = det.model_copy(deep=True)
        d.llm_verdict, d.verification_actions = record, actions
        if question_ambiguous:
            record["overridden"] = "question ambiguity flagged deterministically; kept unresolved"
            return d
        if v.status == "selected" and v.selected_agent in cands:
            c = cands[v.selected_agent]
            blocking = _blocking(c, findings.get(v.selected_agent, []))
            if c.structured_value is None or blocking:
                record["overridden"] = f"selected candidate rejected by guards: {blocking or ['no executed result']}"
                return d
            other = next((n for n in cands if n != v.selected_agent), None)
            agree = det.agreement_status in ("exact_match", "semantic_match")
            d.selected_path = v.selected_agent if not agree else "Reconciled_Consensus"
            if not agree:
                d.agreement_status = "disagreement_resolved_by_adjudicator" if not actions else "resolved_after_verification"
            d.final_answer = c.final_answer_candidate
            d.final_structured_data = c.observed_results
            d.winning_query, d.winning_source_id = c.query_or_plan, c.source_id
            d.verification_status = VerificationStatus.VERIFIED_FACT
            d.confidence_score = max(d.confidence_score, 0.7)
            d.disagreement_analysis = ((d.disagreement_analysis or "") + f" | adjudicator ({self.provider.model}): {v.rationale[:400]}").strip(" |")
            if other and not agree:
                d.unresolved_assumptions = list(d.unresolved_assumptions) + [f"{other}'s candidate rejected: {v.disagreement or ''}"[:300]]
            return d
        if v.status in ("unresolved", "needs_verification"):
            if det.verification_status == VerificationStatus.VERIFIED_FACT and det.agreement_status in ("exact_match", "semantic_match"):
                record["overridden"] = "deterministic consensus on identical executed results kept; adjudicator concern recorded"
                d.unresolved_assumptions = list(d.unresolved_assumptions) + [f"adjudicator concern: {v.rationale[:300]}"]
                return d
            d.selected_path, d.agreement_status = "Abstained_With_Uncertainty", "material_uncertainty"
            d.verification_status = VerificationStatus.INFERRED_CANDIDATE
            d.final_answer = ("Unresolved: the candidates' evidence does not support a single answer. " + v.rationale[:500] +
                              " Candidates: " + "; ".join(f"{n}: {c.final_answer_candidate}" for n, c in cands.items()))
            d.final_structured_data = [{"agent": n, "value": c.structured_value} for n, c in cands.items()]
            d.winning_query = ""
        return d
