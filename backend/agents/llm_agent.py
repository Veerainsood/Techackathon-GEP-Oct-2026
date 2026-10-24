"""LLM-driven investigation loop shared by Explorer and Hercules (different prompts and contexts).

The model selects every tool call itself.  The loop:
  * exposes the shared tool API as function tools; each call must name the knowledge gap it targets
  * returns bounded real outputs (rows, column names, types, aggregates, verbatim errors), wrapped as
    untrusted data so retrieved text cannot act as instructions
  * accepts a structured ``CandidateSubmission`` (Pydantic-validated); invalid submissions or SQL that
    fails on re-execution are sent back as errors for revision
  * enforces turn and tool-call budgets and records usage, cost and an event stream
The system re-executes the submitted SQL itself; the model's claimed results are never trusted.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from backend.agents.planner import plan_from_sql, structured_from_rows
from backend.contracts.models import EvidenceReference, ExplorerInvestigationState
from backend.llm.base import LLMProvider, LLMResponse, LLMUnavailable, ToolSpec, Usage
from backend.skills.tools import ToolBudgetExceeded, ToolSession

MAX_ROWS_TO_MODEL = 20


class SubmittedFilter(BaseModel):
    column: str
    operator: str
    value: Optional[str] = None
    phrase_in_question: str = ""
    evidence: str = Field("", description="Which tool observation showed this value exists")


class InvestigationStateModel(BaseModel):
    known_facts: List[str] = Field(default_factory=list)
    unknown_facts: List[str] = Field(default_factory=list)
    hypotheses: List[str] = Field(default_factory=list)
    observations: List[str] = Field(default_factory=list)
    evidence_references: List[str] = Field(default_factory=list)
    next_action: Dict[str, Any] = Field(default_factory=dict)
    verification_status: str = "pending"


class CandidateSubmission(BaseModel):
    interpreted_question: str
    source_id: str
    sql: str = Field(description="One read-only SELECT; alias the answer column as value")
    metric_interpretation: str
    filters: List[SubmittedFilter] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    abstain: bool = Field(False, description="True if the data cannot disambiguate the question")
    ambiguity_reason: Optional[str] = None
    answer_summary: str
    state: InvestigationStateModel = Field(default_factory=InvestigationStateModel)


def _schema(props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    props = dict(props, gap={"type": "string", "description": "The knowledge gap this call resolves"})
    return {"type": "object", "properties": props, "required": ["gap"] + required}


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
TOOL_SPECS: Dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("discover_sources", "List data sources you may query, with SQL dialect and collection counts.", _schema({}, [])),
    ToolSpec("discover_collections", "Retrieve collections relevant to a natural-language query (skill retrieval).",
             _schema({"query": S, "source_id": S, "k": I}, ["query"])),
    ToolSpec("list_collection_contexts", "List generated reference files available for a source.", _schema({"source_id": S}, ["source_id"])),
    ToolSpec("load_collection_context", "Load the reference file for ONE collection: schema, keys, profiles, values, relationships, "
             "metrics, verified queries. Load only what you need.",
             _schema({"source_id": S, "collection": S, "include_value_profile": B}, ["source_id", "collection"])),
    ToolSpec("describe_collection", "Live schema + profiles of one collection.", _schema({"source_id": S, "collection": S}, ["source_id", "collection"])),
    ToolSpec("inspect_column", "Live profile of one column (nulls, distinct, min/max, top values).",
             _schema({"source_id": S, "collection": S, "column": S}, ["source_id", "collection", "column"])),
    ToolSpec("search_column_values", "Find exact stored values matching a phrase (optionally in one collection/column).",
             _schema({"source_id": S, "term": S, "collection": S, "column": S}, ["source_id", "term"])),
    ToolSpec("sample_rows", "Return a few real rows.", _schema({"source_id": S, "collection": S, "n": I}, ["source_id", "collection"])),
    ToolSpec("explore_relationships", "Verified joins (with fan-out and null keys) and reconciliation rules for a collection.",
             _schema({"source_id": S, "collection": S}, ["source_id", "collection"])),
    ToolSpec("lookup_knowledge", "Search concepts, metric definitions, verified query patterns and learned value mappings.",
             _schema({"query": S}, ["query"])),
    ToolSpec("execute_query", "Run ONE read-only SELECT. Returns columns, types, up to 20 rows, row count, errors, provenance.",
             _schema({"source_id": S, "query": S}, ["source_id", "query"])),
    ToolSpec("explain_query", "Show the query plan.", _schema({"source_id": S, "query": S}, ["source_id", "query"])),
    ToolSpec("execute_federated_query", "Combine bounded results from several sources: parts={alias:{source_id,query}}, "
             "combine_sql is DuckDB SQL over the aliases.",
             _schema({"parts": {"type": "object"}, "combine_sql": S}, ["parts", "combine_sql"])),
    ToolSpec("run_python", "Run Python 3 in your own sandbox (pandas/duckdb available). Pass file_ids to make uploaded files "
             "available; their sandbox paths are returned and printed. Write report files to the 'out/' folder to return them "
             "as downloadable artifacts. Use for file inspection, custom computation or complex transformations, not for "
             "simple SQL questions.",
             _schema({"code": S, "file_ids": {"type": "array", "items": S}}, ["code"])),
]}


def available_tools(session: ToolSession) -> List[str]:
    # Only the data-prep agent may use the sandbox; the investigating agents query data through the gateway.
    return [t for t in TOOL_SPECS if t != "run_python"]
SUBMIT = ToolSpec("submit_candidate", "Submit your final candidate answer (structured). Call exactly once when done.",
                  CandidateSubmission.model_json_schema())


def _escape(text: str) -> str:
    return text.replace("</untrusted_data", "<\\/untrusted_data")


def serialize_result(tool: str, result: Any) -> str:
    """Bounded, informative tool output for the model, wrapped as untrusted data."""
    if isinstance(result, dict) and "succeeded" in result and "rows" in result:
        body = {k: result.get(k) for k in ("succeeded", "error_message", "columns", "column_types", "row_count", "truncated",
                                           "provenance", "execution_time_ms") if k in result}
        body["rows"] = (result.get("rows") or [])[:MAX_ROWS_TO_MODEL]
        if (result.get("row_count") or 0) > MAX_ROWS_TO_MODEL:
            body["note"] = f"showing {MAX_ROWS_TO_MODEL} of {result['row_count']} rows"
        text = json.dumps(body, default=str)
    elif isinstance(result, dict) and "content" in result and result.get("trust") == "untrusted_data":
        meta = {k: v for k, v in result.items() if k not in ("content", "value_profile")}
        text = json.dumps(meta) + "\n" + result["content"][:12000] + (("\n" + result["value_profile"][:4000]) if result.get("value_profile") else "")
    else:
        text = json.dumps(result, default=str)[:8000]
    return f'<untrusted_data tool="{tool}">\n{_escape(text)}\n</untrusted_data>'


class LLMInvestigation:
    def __init__(self, provider: LLMProvider, role: str, system_prompt: str, session: ToolSession,
                 max_turns: int = 24, on_event: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.provider = provider
        self.role = role
        self.system = system_prompt
        self.session = session
        self.max_turns = max_turns
        self.on_event = on_event or (lambda e: None)
        self.usage = Usage()
        self.transcript: List[Dict[str, Any]] = []

    def _emit(self, kind: str, **data: Any) -> None:
        self.on_event({"agent": self.role, "event": kind, "ts": round(time.time(), 3), **data})

    async def _dispatch(self, name: str, args: Dict[str, Any], turn: int) -> Tuple[str, bool]:
        args = dict(args)
        gap = str(args.pop("gap", "")).strip()
        if not gap:
            return "Error: every tool call must include a non-empty 'gap' naming the knowledge gap it resolves.", True
        if name == "execute_query":
            args.setdefault("max_rows", 50)
        try:
            result = await asyncio.to_thread(self.session.call, name, gap, f"LLM_TURN_{turn}", **args)
        except ToolBudgetExceeded:
            raise
        except TypeError as exc:
            return f"Error: bad arguments for {name}: {exc}", True
        except Exception as exc:  # access denied, unknown collection, ... are observations, not crashes
            return f"Error: {type(exc).__name__}: {str(exc)[:400]}", True
        is_err = isinstance(result, dict) and result.get("succeeded") is False
        return serialize_result(name, result), is_err

    async def run(self, user_content: str, tools: List[str]) -> Tuple[Optional[CandidateSubmission], Dict[str, Any]]:
        specs = [TOOL_SPECS[t] for t in tools if t in TOOL_SPECS] + [SUBMIT]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": user_content}]}]
        submission: Optional[CandidateSubmission] = None
        final_exec: Dict[str, Any] = {}
        budget_hit = False
        for turn in range(self.max_turns):
            only_submit = budget_hit or turn == self.max_turns - 1
            resp: LLMResponse = await self.provider.acomplete(
                self.system, messages, [SUBMIT] if only_submit else specs,
                tool_choice="submit_candidate" if only_submit else None, usage=self.usage)
            messages.append({"role": "assistant", "content": resp.raw_content or [{"type": "text", "text": resp.text or "(no content)"}]})
            if resp.text:
                self._emit("model_text", turn=turn, text=resp.text[:500])
            if not resp.tool_calls:
                messages.append({"role": "user", "content": [{"type": "text", "text":
                                 "Continue the investigation with a tool call, or call submit_candidate when the answer is grounded."}]})
                continue
            results = []
            for call in resp.tool_calls:  # tool executions themselves are streamed by the ToolSession
                if call.name == "submit_candidate":
                    try:
                        sub = CandidateSubmission.model_validate(call.arguments)
                    except ValidationError as exc:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                        "content": f"Invalid submission: {exc.errors()[:5]}"})
                        continue
                    if sub.abstain and not sub.sql.strip():
                        submission = sub
                        results.append({"type": "tool_result", "tool_use_id": call.id, "content": "abstention recorded"})
                        continue
                    check = await asyncio.to_thread(self.session.toolbox.execute_query, self.session.ctx, sub.source_id, sub.sql, 200)
                    if not check["succeeded"]:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                        "content": serialize_result("submit_candidate", check) +
                                        "\nYour SQL failed on re-execution. Fix it and submit again."})
                        self._emit("submission_rejected", turn=turn, error=check.get("error_message"))
                        continue
                    submission, final_exec = sub, check
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": "accepted"})
                    self._emit("submission", turn=turn, sql=sub.sql, source_id=sub.source_id, rows=(check.get("rows") or [])[:8],
                               columns=check.get("columns"), interpretation=sub.interpreted_question)
                    continue
                if call.name not in tools:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": f"Tool {call.name} is not available to you."})
                    continue
                try:
                    content, is_err = await self._dispatch(call.name, call.arguments, turn)
                except ToolBudgetExceeded as exc:
                    budget_hit = True
                    content, is_err = f"Tool budget exhausted ({exc}). Submit your best grounded candidate now.", True
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": content, "is_error": is_err})
            messages.append({"role": "user", "content": results})
            if submission is not None:
                break
        self.transcript = messages
        self._emit("finished", submitted=submission is not None, usage=self.usage.as_dict())
        return submission, final_exec


def candidate_from_submission(agent: str, question: str, sub: Optional[CandidateSubmission], final_exec: Dict[str, Any],
                              session: ToolSession, usage: Usage, latency_ms: float, collections: List[str],
                              dialect: Optional[str], context_tokens: int = 0):
    """Turn a validated submission (and the system's own re-execution) into an AgentReasoningCandidate."""
    from backend.agents.common import evidence_from_result, interpret
    from backend.agents.planner import QueryPlan
    from backend.contracts.models import AgentReasoningCandidate, ValidationFinding

    state = ExplorerInvestigationState(user_requirements=[question])
    if sub is None:
        return AgentReasoningCandidate(agent_name=agent, interpreted_question="(no submission)", query_or_plan="", source_id="",
                                       final_answer_candidate="No candidate submitted within budget", structured_value=None,
                                       resolved_entities_and_metrics={"plan": QueryPlan(source_id="", primary_collection="",
                                                                                        aggregation="LIST").model_dump()},
                                       tool_calls_count=len(session.observations), reasoning_mode="llm",
                                       llm_usage=usage.as_dict(), failure="no_submission", latency_ms=round(latency_ms, 1))
    try:
        plan = plan_from_sql(sub.sql, sub.source_id, dialect, collections) if sub.sql.strip() else \
            QueryPlan(source_id=sub.source_id, primary_collection="", aggregation="LIST")
    except Exception as exc:
        plan = QueryPlan(source_id=sub.source_id, primary_collection="", aggregation="LIST", rationale=[f"unparsed SQL: {exc}"])
    for f in plan.filters:
        for sf in sub.filters:
            if sf.column == f.column and sf.phrase_in_question:
                f.matched_text = sf.phrase_in_question
    if sub.abstain:
        plan.ambiguities.append({"type": "agent_abstained", "detail": sub.ambiguity_reason or "agent judged the question ambiguous"})
    plan.assumptions = list(sub.assumptions)
    rows = final_exec.get("rows") or []
    value = structured_from_rows(rows) if final_exec.get("succeeded") else None
    st = sub.state
    state.known_facts, state.unknown_facts, state.hypotheses = st.known_facts, st.unknown_facts, st.hypotheses
    state.tool_observations = session.observations
    state.validation_outcomes = [f"model verification status: {st.verification_status}"]
    state.remaining_ambiguities = sub.open_questions
    evidence: List[EvidenceReference] = []
    for o in session.observations:
        if o.tool_name in ("execute_query", "search_column_values", "sample_rows", "inspect_column"):
            evidence.append(EvidenceReference(evidence_id=f"ev:{agent}:{o.step_index}", source_id=sub.source_id,
                                              evidence_type="query_result" if o.tool_name == "execute_query" else "column_profile",
                                              query_or_action=f"{o.tool_name}({json.dumps(o.tool_args, default=str)[:300]})",
                                              observed_summary=o.observation_summary))
    if final_exec:
        evidence.append(evidence_from_result(agent, sub.source_id, sub.sql, final_exec))
    checks = [ValidationFinding(validator_name=f"{agent.lower()}_reexecution", passed=bool(final_exec.get("succeeded")),
                                severity="info" if final_exec.get("succeeded") else "error",
                                message=f"system re-executed submitted SQL: {final_exec.get('row_count')} rows")]
    return AgentReasoningCandidate(
        agent_name=agent, interpreted_question=sub.interpreted_question or interpret(plan),
        resolved_entities_and_metrics={"plan": plan.model_dump(), "measure": plan.measure, "primary": plan.primary_collection,
                                       "aggregation": plan.aggregation, "group_by": plan.group_by,
                                       "metric_interpretation": sub.metric_interpretation},
        applied_filters=[f.model_dump() for f in plan.filters],
        source_mappings=[{"term": f.phrase_in_question, "maps_to": f.column, "value": str(f.value), "origin": "llm_grounded",
                          "evidence": f.evidence} for f in sub.filters],
        query_or_plan=sub.sql, source_id=sub.source_id, observed_results=rows[:50], evidence_references=evidence,
        verification_results=checks, open_questions=list(dict.fromkeys(sub.open_questions + sub.assumptions)),
        final_answer_candidate=sub.answer_summary, structured_value=value, tool_calls_count=len(session.observations),
        tokens_used=usage.input_tokens + usage.output_tokens + context_tokens, latency_ms=round(latency_ms, 1),
        investigation_trace=state, reasoning_mode="llm", llm_usage=usage.as_dict(),
        result_provenance=final_exec.get("provenance") or {})
