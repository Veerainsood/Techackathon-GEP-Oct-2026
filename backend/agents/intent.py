"""Intent agent: the first step for every question.

It decides, with the same read-only knowledge tools the investigating agents use plus a fixed capability list, whether a
message can be answered directly (capabilities, available data, field meanings, freshness), needs a clarifying question,
or is a data question for Explorer and Hercules -- in which case it hands them a brief. Only the investigation path
produces dual-verified business figures; a direct answer that needed a query is labelled as not dual-verified.

Without an LLM it falls back to deterministic rules that only answer obvious conversational and catalog questions and
send everything else to the investigation.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from backend.llm.base import LLMProvider, ToolSpec, Usage
from backend.skills.tools import ToolBudgetExceeded, ToolSession

CAPABILITIES: Dict[str, Any] = json.loads((Path(__file__).with_name("capabilities.json")).read_text(encoding="utf-8"))
INTENT_TOOLS = ["discover_sources", "discover_collections", "describe_collection", "inspect_column", "search_column_values",
                "sample_rows", "explore_relationships", "lookup_knowledge", "execute_query"]


class IntentBrief(BaseModel):
    interpreted_question: str
    sources: List[str] = Field(default_factory=list)
    collections: List[str] = Field(default_factory=list)
    notes: str = ""


class IntentDecision(BaseModel):
    route: Literal["answer", "clarify", "investigate"]
    reason: str
    answer: Optional[str] = Field(None, description="The reply, for route=answer")
    clarifying_question: Optional[str] = Field(None, description="For route=clarify")
    options: List[str] = Field(default_factory=list, description="2-4 choices for route=clarify")
    brief: Optional[IntentBrief] = Field(None, description="For route=investigate")
    standalone_question: Optional[str] = Field(
        None, description="For route=investigate: the message rewritten to stand alone, resolving references to earlier turns "
                          "or attached files (e.g. 'what about last year?' -> 'What was total invoiced revenue in 2024?')")


SUBMIT = ToolSpec("submit_intent", "Submit how this message should be handled. Call exactly once.", IntentDecision.model_json_schema())

CONVERSATIONAL = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon|evening)|who are you|what can you do|"
                            r"what do you do|help|how do you work|what are your capabilities)\b[\s!?.]*", re.I)
CATALOG = re.compile(r"\b(what|which)\s+(data|data sources|sources|databases|tables|collections)\b.*\b(have|access|available|"
                     r"connected|know|use)\b", re.I)
FRESHNESS = re.compile(r"\b(how fresh|freshness|last (sync|synced|updated)|up to date)\b", re.I)


def capability_text() -> str:
    can = "\n".join(f"- {c['description']}" for c in CAPABILITIES["can"])
    cannot = "\n".join(f"- {c}" for c in CAPABILITIES["cannot"])
    return f"I can:\n{can}\nI cannot:\n{cannot}"


class IntentAgent:
    name = "Intent"

    def __init__(self, llm: Optional[LLMProvider] = None, max_turns: int = 6):
        self.llm = llm
        self.max_turns = max_turns

    def decide(self, question: str, session: ToolSession, freshness: List[Dict[str, Any]],
               on_event: Optional[Callable[[Dict[str, Any]], None]] = None, history: Optional[List[Dict[str, str]]] = None,
               files: Optional[List[Dict[str, Any]]] = None) -> Tuple[IntentDecision, Dict[str, Any]]:
        """Returns the decision and metadata: mode, usage, whether a query was executed.

        ``history`` is the conversation so far (role/content, oldest first); ``files`` are the attached files.
        """
        if self.llm is not None:
            try:
                return asyncio.run(self._decide_llm(question, session, freshness, on_event or (lambda e: None),
                                                    history or [], files or []))
            except Exception as exc:  # model unavailable or invalid output: fall back, and say so
                decision, meta = self._decide_rules(question, session, freshness)
                meta.update(mode="deterministic_fallback", failure=f"{type(exc).__name__}: {str(exc)[:200]}")
                return decision, meta
        return self._decide_rules(question, session, freshness)

    # ------------------------------------------------------------------ deterministic rules
    def _decide_rules(self, question: str, session: ToolSession, freshness: List[Dict[str, Any]]) -> Tuple[IntentDecision, Dict[str, Any]]:
        meta = {"mode": "deterministic", "queried": False}
        if CONVERSATIONAL.fullmatch(question) or CONVERSATIONAL.match(question) and len(question.split()) <= 6:
            sources = session.call("discover_sources", objective="list connected data for a capabilities answer", phase="INTENT")
            data = "; ".join(f"{s['name']} ({s['collections']} collections)" for s in sources)
            return IntentDecision(route="answer", reason="conversational or capability question",
                                  answer=f"{capability_text()}\nConnected data: {data}."), meta
        if CATALOG.search(question):
            sources = session.call("discover_sources", objective="list connected data sources", phase="INTENT")
            lines = [f"- {s['name']} ({s['source_id']}): {s['collections']} collections" for s in sources]
            return IntentDecision(route="answer", reason="question about the available data",
                                  answer="Connected data sources:\n" + "\n".join(lines)), meta
        if FRESHNESS.search(question) and freshness:
            lines = [f"- {f['name']}: {f['sync_status']}, last sync {f.get('last_sync_at') or 'no sync events yet'}" for f in freshness]
            return IntentDecision(route="answer", reason="question about data freshness",
                                  answer="Data freshness:\n" + "\n".join(lines)), meta
        return IntentDecision(route="investigate", reason="data question", brief=IntentBrief(interpreted_question=question)), meta

    # ------------------------------------------------------------------ LLM
    async def _decide_llm(self, question: str, session: ToolSession, freshness: List[Dict[str, Any]],
                          emit: Callable[[Dict[str, Any]], None], history: List[Dict[str, str]],
                          files: List[Dict[str, Any]]) -> Tuple[IntentDecision, Dict[str, Any]]:
        from backend.agents.llm_agent import TOOL_SPECS, serialize_result
        from backend.agents.prompts import INTENT_SYSTEM

        usage, queried = Usage(), False
        system = INTENT_SYSTEM.format(capabilities=capability_text())
        catalog = session.call("collection_catalog", objective="what data exists, for routing", phase="INTENT")
        lines = "\n".join(f"- {c['source_id']}:{c['collection']} | rows={c.get('rows')} | {str(c.get('summary', ''))[:100]}"
                          for c in catalog)
        turns = "\n".join(f"{h['role']}: {h['content']}" for h in history)
        attached = [{"filename": f.get("filename"), "collection": f.get("collection")} for f in files]
        user = ((("Conversation so far (oldest first):\n" + serialize_result("conversation", {"turns": turns}) + "\n\n")
                 if history else "")
                + (("Files attached to this conversation (queryable as collections):\n"
                    + serialize_result("attached_files", attached) + "\n\n") if attached else "")
                + f"Message: {question}\n\nCollection catalog:\n" + serialize_result("collection_catalog", {"collections": lines})
                + "\n\nSource freshness:\n" + serialize_result("freshness", freshness))
        messages: List[Dict[str, Any]] = [{"role": "user", "content": [{"type": "text", "text": user}]}]
        specs = [TOOL_SPECS[t] for t in INTENT_TOOLS] + [SUBMIT]
        for turn in range(self.max_turns):
            last = turn == self.max_turns - 1
            resp = await self.llm.acomplete(system, messages, [SUBMIT] if last else specs,
                                            tool_choice="submit_intent" if last else None, max_tokens=1500, usage=usage)
            messages.append({"role": "assistant", "content": resp.raw_content or [{"type": "text", "text": resp.text or "(no content)"}]})
            if resp.text:
                emit({"agent": self.name, "event": "model_text", "ts": round(time.time(), 3), "text": resp.text[:500]})
            if not resp.tool_calls:
                messages.append({"role": "user", "content": [{"type": "text", "text": "Call a tool or submit_intent."}]})
                continue
            results = []
            for call in resp.tool_calls:
                if call.name == "submit_intent":
                    try:
                        decision = IntentDecision.model_validate(call.arguments)
                    except ValidationError as exc:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                        "content": f"Invalid decision: {exc.errors()[:5]}"})
                        continue
                    problem = _incomplete(decision)
                    if problem:
                        results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True, "content": problem})
                        continue
                    return decision, {"mode": "llm", "queried": queried, "usage": usage.as_dict(), "model": self.llm.model}
                if call.name not in INTENT_TOOLS:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": f"Tool {call.name} is not available to you."})
                    continue
                args = dict(call.arguments)
                gap = str(args.pop("gap", "")).strip()
                if not gap:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": "Every tool call needs a non-empty 'gap'."})
                    continue
                try:
                    out = await asyncio.to_thread(session.call, call.name, gap, f"INTENT_{turn}", **args)
                    queried = queried or call.name == "execute_query"
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": serialize_result(call.name, out)})
                except ToolBudgetExceeded:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": "Tool budget exhausted. Submit your decision now."})
                except Exception as exc:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "is_error": True,
                                    "content": f"Error: {type(exc).__name__}: {str(exc)[:300]}"})
            messages.append({"role": "user", "content": results})
        raise RuntimeError("intent agent did not submit a decision")


def _incomplete(d: IntentDecision) -> Optional[str]:
    if d.route == "answer" and not (d.answer or "").strip():
        return "route=answer needs 'answer'."
    if d.route == "clarify" and not (d.clarifying_question or "").strip():
        return "route=clarify needs 'clarifying_question' and options."
    if d.route == "investigate" and d.brief is None:
        return "route=investigate needs a 'brief'."
    return None
