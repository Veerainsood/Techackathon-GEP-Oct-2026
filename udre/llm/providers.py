"""Wire-format providers: Anthropic Messages API and OpenAI-compatible Chat Completions.

Both use plain async httpx so the same code serves first-party endpoints and gateways such as
Azure AI Foundry (``https://<resource>.services.ai.azure.com/anthropic/v1/messages``) or Azure
OpenAI.  Credentials are only placed in request headers and are redacted from every error.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from udre.llm.base import RETRYABLE_STATUS, LLMProvider, LLMResponse, LLMUnavailable, RetryableError, ToolCall, ToolSpec, redact


def _retry_after(resp: httpx.Response) -> Optional[float]:
    for h in ("retry-after", "x-ratelimit-reset-requests"):
        v = resp.headers.get(h)
        if v:
            try:
                return float(v)
            except ValueError:
                return None
    return None


class AnthropicMessagesProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, url: str, api_key: str, model: str, anthropic_version: str = "2023-06-01",
                 transport: Optional[httpx.AsyncBaseTransport] = None, **kw: Any):
        super().__init__(model, **kw)
        self.url = url if url.rstrip("/").endswith("/messages") else url.rstrip("/") + "/v1/messages"
        self._key = api_key
        self._version = anthropic_version
        self._transport = transport

    _omit_temperature = False

    async def _call(self, system, messages, tools, tool_choice, max_tokens) -> LLMResponse:
        body: Dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if not self._omit_temperature:
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
            if tool_choice:
                body["tool_choice"] = {"type": "tool", "name": tool_choice} if tool_choice not in ("any", "auto") else {"type": tool_choice}
        # x-api-key for first-party Anthropic, api-key for Azure AI Foundry; sending both is harmless.
        headers = {"x-api-key": self._key, "api-key": self._key, "anthropic-version": self._version,
                   "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                resp = await client.post(self.url, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableError(f"transport error: {type(exc).__name__}") from exc
        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableError(f"HTTP {resp.status_code}", _retry_after(resp))
        if resp.status_code == 400 and "temperature" in resp.text and not self._omit_temperature:
            self._omit_temperature = True  # some deployments reject sampling parameters; retry once without them
            return await self._call(system, messages, tools, tool_choice, max_tokens)
        if resp.status_code >= 400:
            raise LLMUnavailable(f"HTTP {resp.status_code}: {redact(resp.text[:300], [self._key])}")
        data = resp.json()
        text, calls = [], []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text.append(block["text"])
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}))
        usage = data.get("usage", {})
        return LLMResponse(text="\n".join(text), tool_calls=calls, stop_reason=data.get("stop_reason", ""),
                           input_tokens=int(usage.get("input_tokens", 0)), output_tokens=int(usage.get("output_tokens", 0)),
                           raw_content=data.get("content", []))


class OpenAIChatProvider(LLMProvider):
    """OpenAI-compatible ``/chat/completions`` (OpenAI, Azure OpenAI, local gateways)."""

    name = "openai"

    def __init__(self, url: str, api_key: str, model: str, transport: Optional[httpx.AsyncBaseTransport] = None, **kw: Any):
        super().__init__(model, **kw)
        self.url = url if url.rstrip("/").endswith("/chat/completions") else url.rstrip("/") + "/chat/completions"
        self._key = api_key
        self._transport = transport

    @staticmethod
    def _to_openai(system: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            blocks = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
            if m["role"] == "assistant":
                msg: Dict[str, Any] = {"role": "assistant", "content": "\n".join(b["text"] for b in blocks if b["type"] == "text") or None}
                tcs = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                       for b in blocks if b["type"] == "tool_use"]
                if tcs:
                    msg["tool_calls"] = tcs
                out.append(msg)
            else:
                texts = [b["text"] for b in blocks if b["type"] == "text"]
                for b in blocks:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
        return out

    async def _call(self, system, messages, tools, tool_choice, max_tokens) -> LLMResponse:
        body: Dict[str, Any] = {"model": self.model, "max_completion_tokens": max_tokens, "messages": self._to_openai(system, messages)}
        if not self.model.lower().startswith(("gpt-5", "o1", "o3", "o4")):  # reasoning models only accept the default
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.input_schema}} for t in tools]
            if tool_choice and tool_choice not in ("auto", "any"):
                body["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
            elif tool_choice == "any":
                body["tool_choice"] = "required"
        headers = {"authorization": f"Bearer {self._key}", "api-key": self._key, "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                resp = await client.post(self.url, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise RetryableError(f"transport error: {type(exc).__name__}") from exc
        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableError(f"HTTP {resp.status_code}", _retry_after(resp))
        if resp.status_code >= 400:
            raise LLMUnavailable(f"HTTP {resp.status_code}: {redact(resp.text[:300], [self._key])}")
        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {"_unparseable_arguments": tc["function"]["arguments"]}
            calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args))
        raw = ([{"type": "text", "text": msg["content"]}] if msg.get("content") else []) + \
              [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in calls]
        usage = data.get("usage", {})
        return LLMResponse(text=msg.get("content") or "", tool_calls=calls, stop_reason=choice.get("finish_reason", ""),
                           input_tokens=int(usage.get("prompt_tokens", 0)), output_tokens=int(usage.get("completion_tokens", 0)),
                           raw_content=raw)
