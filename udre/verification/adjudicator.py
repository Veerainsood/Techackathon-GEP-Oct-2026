"""Evidence-based adjudication between independent candidates.

Decision rules (no rhetorical confidence is consulted):
  * score = deterministic findings (errors, warnings, reconciliations, grounded filters)
  * agreement is decided on executed results (numeric tolerance), not on wording
  * disagreements are localized (measure / collection / filters / joins / aggregation) and probed
    with discriminating queries; a candidate wins only with a clear evidence margin
  * ambiguity flagged by the question itself (competing metric definitions, relative periods)
    or an unresolved disagreement yields an explicit abstention with the alternatives
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from udre.agents.planner import CatalogView, QueryPlan, _lit
from udre.contracts.models import (
    AdjudicationDecision,
    AgentReasoningCandidate,
    EvidenceReference,
    ValidationFinding,
    VerificationStatus,
)
from udre.gateway.registry import AccessContext, SourceGateway
from udre.skills.tools import ToolBox


def evidence_score(findings: Sequence[ValidationFinding], candidate: AgentReasoningCandidate) -> Tuple[float, List[str]]:
    score, notes = 0.0, []
    for f in list(findings) + list(candidate.verification_results):
        if not f.passed and f.severity == "error":
            score -= 3.0
            notes.append(f"-3 {f.validator_name}: {f.message}")
        elif f.severity == "warning":
            score -= 0.5
            notes.append(f"-0.5 {f.validator_name}: {f.message}")
        elif f.validator_name == "reconciliation" and f.passed:
            score += 2.0
            notes.append(f"+2 reconciliation: {f.message}")
        elif f.validator_name in ("filter_grounding", "explorer_filter_presence") and f.passed:
            score += 0.5
    if candidate.structured_value is None:
        score -= 5.0
        notes.append("-5 no result value")
    return score, notes


def values_agree(a: Any, b: Any, rel: float = 0.001, abs_tol: float = 0.01) -> bool:
    if a is None or b is None:
        return False
    if isinstance(a, dict) and isinstance(b, dict):
        if len(a) != len(b):
            return False
        va, vb = sorted(float(x or 0) for x in a.values()), sorted(float(x or 0) for x in b.values())
        return all(abs(x - y) <= max(abs_tol, rel * max(abs(x), abs(y))) for x, y in zip(va, vb))
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    return abs(x - y) <= max(abs_tol, rel * max(abs(x), abs(y)))


def plan_of(c: AgentReasoningCandidate) -> QueryPlan:
    return QueryPlan(**c.resolved_entities_and_metrics["plan"])


def diff_plans(a: QueryPlan, b: QueryPlan) -> List[str]:
    out = []
    if a.source_id != b.source_id:
        out.append(f"source: {a.source_id} vs {b.source_id}")
    if a.measure != b.measure:
        out.append(f"measure: {a.measure} vs {b.measure}")
    if a.primary_collection != b.primary_collection:
        out.append(f"primary collection: {a.primary_collection} vs {b.primary_collection}")
    if a.aggregation != b.aggregation:
        out.append(f"aggregation: {a.aggregation} vs {b.aggregation}")
    fa = {(f.collection, f.column, f.op, str(f.value)) for f in a.filters}
    fb = {(f.collection, f.column, f.op, str(f.value)) for f in b.filters}
    if fa - fb:
        out.append(f"filters only in A: {sorted(fa - fb)}")
    if fb - fa:
        out.append(f"filters only in B: {sorted(fb - fa)}")
    if [tuple(g) for g in a.group_by] != [tuple(g) for g in b.group_by]:
        out.append(f"grouping: {a.group_by} vs {b.group_by}")
    ja = {(j.left, j.right) for j in a.joins}
    jb = {(j.left, j.right) for j in b.joins}
    if ja != jb:
        out.append(f"joins: {sorted(ja)} vs {sorted(jb)}")
    return out


class Adjudicator:
    def __init__(self, gateway: SourceGateway, toolbox: ToolBox, margin: float = 2.0):
        self.gateway = gateway
        self.toolbox = toolbox
        self.margin = margin

    def discriminating_checks(self, ctx: AccessContext, a: QueryPlan, b: QueryPlan) -> List[Dict[str, Any]]:
        """Targeted queries that expose *why* the candidates differ."""
        checks = []
        fa = {(f.collection, f.column, f.op, str(f.value)): f for f in a.filters}
        fb = {(f.collection, f.column, f.op, str(f.value)): f for f in b.filters}
        for key in sorted(set(fa) ^ set(fb)):
            f = fa.get(key) or fb.get(key)
            if f.op in ("=", "<>"):
                plan = a if key in fa else b
                sql = f'SELECT COUNT(*) AS rows_with_value FROM {_q(f.collection)} WHERE {_q(f.column)} = {_lit(f.value)}'
                res = self.gateway.execute_query(ctx, plan.source_id, sql, max_rows=5)
                checks.append({"purpose": f"does filter value {f.column}={f.value} (only in {'A' if key in fa else 'B'}) exist?",
                               "sql": sql, "rows": res.rows, "ok": res.succeeded})
        for label, plan in (("A", a), ("B", b)):
            if plan.measure:
                coll, col = plan.measure
                sql = f'SELECT COUNT(*) AS n, COUNT({_q(col)}) AS non_null, SUM({_q(col)}) AS total FROM {_q(coll)}'
                res = self.gateway.execute_query(ctx, plan.source_id, sql, max_rows=5)
                checks.append({"purpose": f"unfiltered profile of {label}'s measure {coll}.{col}", "sql": sql,
                               "rows": res.rows, "ok": res.succeeded})
        return checks

    def adjudicate(self, ctx: AccessContext, question: str, cands: Dict[str, AgentReasoningCandidate],
                   findings: Dict[str, List[ValidationFinding]]) -> AdjudicationDecision:
        names = [n for n in ("Explorer", "Hercules") if n in cands]
        scores = {n: evidence_score(findings.get(n, []), cands[n]) for n in names}
        lineage = [f"{n}: {cands[n].query_or_plan}" for n in names]
        seen_f: set = set()
        all_findings = []
        for n in names:
            for f in findings.get(n, []):
                if (f.validator_name, f.message) not in seen_f:
                    seen_f.add((f.validator_name, f.message))
                    all_findings.append(f)
        evidence: List[EvidenceReference] = [e for n in names for e in cands[n].evidence_references]
        total_tokens = sum(c.tokens_used for c in cands.values())
        latency = max((c.latency_ms for c in cands.values()), default=0.0)
        ambiguous = [q for n in names for q in cands[n].open_questions
                     if "several measures" in q or "interpreted as calendar year" in q]

        if len(names) == 1:
            n = names[0]
            return self._decide(question, cands[n], n, "single_path", None, None, findings.get(n, []), all_findings, evidence, lineage,
                                scores[n][0], ambiguous, total_tokens, latency)
        a, b = cands["Explorer"], cands["Hercules"]
        pa, pb = plan_of(a), plan_of(b)
        agree = values_agree(a.structured_value, b.structured_value)
        diffs = diff_plans(pa, pb)
        if agree:
            winner = "Explorer" if scores["Explorer"][0] >= scores["Hercules"][0] else "Hercules"
            status = "exact_match" if not diffs else "semantic_match"
            analysis = None if not diffs else "different formulations, same result: " + "; ".join(diffs)
            return self._decide(question, cands[winner], "Reconciled_Consensus", status, analysis, None, findings.get(winner, []), all_findings,
                                evidence, lineage, max(s[0] for s in scores.values()), ambiguous, total_tokens, latency)
        checks = self.discriminating_checks(ctx, pa, pb)
        analysis = ("results differ: " + f"Explorer={_short(a.structured_value)} vs Hercules={_short(b.structured_value)}; "
                    + "; ".join(diffs) + " | evidence scores: "
                    + ", ".join(f"{n}={scores[n][0]:.1f}" for n in names))
        sa, sb = scores["Explorer"][0], scores["Hercules"][0]
        if abs(sa - sb) >= self.margin:
            winner = "Explorer" if sa > sb else "Hercules"
            return self._decide(question, cands[winner], winner, "disagreement_resolved", analysis, checks, findings.get(winner, []),
                                all_findings, evidence, lineage, max(sa, sb), ambiguous, total_tokens, latency,
                                loser_notes=scores["Hercules" if winner == "Explorer" else "Explorer"][1])
        return self._abstain(question, a, b, analysis, checks, all_findings, evidence, lineage, total_tokens, latency)

    # ------------------------------------------------------------------ decisions
    def _decide(self, question, cand, path, status, analysis, checks, winner_findings, findings, evidence, lineage, score,
                ambiguous, tokens, latency, loser_notes=None) -> AdjudicationDecision:
        winner_errors = [f for f in list(cand.verification_results) + list(winner_findings)
                         if not f.passed and f.severity == "error"]
        warnings = [f.message for f in findings if f.severity == "warning"]
        assumptions = list(dict.fromkeys(cand.open_questions + warnings))
        if ambiguous:
            return AdjudicationDecision(
                question=question, selected_path="Abstained_With_Uncertainty", agreement_status="material_uncertainty",
                disagreement_analysis=(analysis or "") + " | question admits several definitions: " + "; ".join(dict.fromkeys(ambiguous)),
                discriminating_check_query=None, final_answer=(
                    "The question is ambiguous, so no single verified answer is given. Best-supported reading: "
                    f"{cand.final_answer_candidate} (via {cand.interpreted_question}). Please specify: "
                    + "; ".join(dict.fromkeys(ambiguous))),
                final_structured_data=cand.observed_results, winning_query=cand.query_or_plan, winning_source_id=cand.source_id,
                supporting_evidence=evidence[:20], validation_findings=findings, unresolved_assumptions=assumptions,
                confidence_score=0.3, verification_status=VerificationStatus.INFERRED_CANDIDATE, query_lineage=lineage,
                total_latency_ms=latency, total_tokens_used=tokens)
        blocking = winner_errors
        verified = not blocking and cand.structured_value is not None
        confidence = round(max(0.05, min(0.99, 0.75 + 0.05 * score - 0.25 * len(blocking))), 2)
        answer = cand.final_answer_candidate
        if not verified:
            answer = f"Unverified: {answer}. Blocking findings: " + "; ".join(f.message for f in blocking[:3])
        return AdjudicationDecision(
            question=question, selected_path=path, agreement_status=status, disagreement_analysis=analysis,
            discriminating_check_query="\n".join(c["sql"] for c in checks) if checks else None,
            discriminating_check_result=[{"purpose": c["purpose"], "rows": c["rows"]} for c in checks] if checks else None,
            final_answer=answer, final_structured_data=cand.observed_results, winning_query=cand.query_or_plan,
            winning_source_id=cand.source_id, supporting_evidence=evidence[:20], validation_findings=findings,
            unresolved_assumptions=assumptions + ([f"rejected candidate issues: {loser_notes[:3]}"] if loser_notes else []),
            confidence_score=confidence,
            verification_status=VerificationStatus.VERIFIED_FACT if verified else VerificationStatus.INFERRED_CANDIDATE,
            query_lineage=lineage, total_latency_ms=latency, total_tokens_used=tokens)

    def _abstain(self, question, a, b, analysis, checks, findings, evidence, lineage, tokens, latency) -> AdjudicationDecision:
        return AdjudicationDecision(
            question=question, selected_path="Abstained_With_Uncertainty", agreement_status="material_uncertainty",
            disagreement_analysis=analysis, discriminating_check_query="\n".join(c["sql"] for c in checks) if checks else None,
            discriminating_check_result=[{"purpose": c["purpose"], "rows": c["rows"]} for c in checks] if checks else None,
            final_answer=("The two independent analyses disagree and the evidence does not clearly favour either. "
                          f"Explorer: {a.final_answer_candidate} ({a.interpreted_question}). "
                          f"Hercules: {b.final_answer_candidate} ({b.interpreted_question})."),
            final_structured_data=[{"agent": "Explorer", "value": a.structured_value},
                                   {"agent": "Hercules", "value": b.structured_value}],
            winning_query="", winning_source_id=a.source_id, supporting_evidence=evidence[:20], validation_findings=findings,
            unresolved_assumptions=list(dict.fromkeys(a.open_questions + b.open_questions)), confidence_score=0.2,
            verification_status=VerificationStatus.INFERRED_CANDIDATE, query_lineage=lineage, total_latency_ms=latency,
            total_tokens_used=tokens)


def _q(name: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))


def _short(v: Any) -> str:
    s = str(v)
    return s if len(s) < 120 else s[:117] + "..."
