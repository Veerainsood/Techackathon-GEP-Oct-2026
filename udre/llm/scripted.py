"""Deterministic scripted provider -- a TEST DOUBLE, never used unless explicitly constructed.

It lets tests drive the real agent loop (tool dispatch, result serialization, budgets, revision
after errors, structured submission) without a model API.  It is not presented as reasoning.
"""

from __future__ import annotations

import itertools
from typing import Any, Callable, Dict, List, Optional

from udre.llm.base import LLMProvider, LLMResponse, ToolCall, ToolSpec

Policy = Callable[[str, List[Dict[str, Any]], List[ToolSpec]], LLMResponse]


class ScriptedProvider(LLMProvider):
    name = "scripted-test-double"

    def __init__(self, policy: Policy, model: str = "scripted"):
        super().__init__(model, timeout_s=10, max_retries=0)
        self.policy = policy
        self.requests: List[Dict[str, Any]] = []

    async def _call(self, system, messages, tools, tool_choice, max_tokens) -> LLMResponse:
        self.requests.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t.name for t in tools],
                              "tool_choice": tool_choice})
        resp = self.policy(system, messages, tools)
        resp.input_tokens = resp.input_tokens or sum(len(str(m["content"])) for m in messages) // 4
        resp.output_tokens = resp.output_tokens or 20
        resp.raw_content = ([{"type": "text", "text": resp.text}] if resp.text else []) + \
                           [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in resp.tool_calls]
        return resp


_ids = itertools.count(1)


def tool(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(text="", tool_calls=[ToolCall(id=f"call_{next(_ids)}", name=name, arguments=arguments)], stop_reason="tool_use")


def last_tool_result(messages: List[Dict[str, Any]]) -> Optional[str]:
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    return b["content"]
    return None
