"""Neutral message/tool representation and the provider contract.

Messages use content blocks (text / tool_use / tool_result) -- the Anthropic shape -- and each
provider translates to its wire format.  Providers are async; retries, timeouts and usage
accounting live here so every provider behaves the same.
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class LLMUnavailable(RuntimeError):
    """Provider unreachable, unauthorized or out of retries; callers may fall back to offline mode."""


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class LLMResponse:
    text: str
    tool_calls: List[ToolCall]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_content: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    retries: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, resp: LLMResponse, latency_ms: float, in_cost: float, out_cost: float, retries: int) -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += resp.input_tokens
            self.output_tokens += resp.output_tokens
            self.cost_usd += resp.input_tokens / 1e6 * in_cost + resp.output_tokens / 1e6 * out_cost
            self.latency_ms += latency_ms
            self.retries += retries

    def as_dict(self) -> Dict[str, Any]:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cost_usd": round(self.cost_usd, 6), "latency_ms": round(self.latency_ms, 1), "retries": self.retries}


RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class LLMProvider(ABC):
    name = "abstract"

    def __init__(self, model: str, timeout_s: float = 60.0, max_retries: int = 3, max_tokens: int = 2048,
                 temperature: float = 0.0, input_cost_per_mtok: float = 0.0, output_cost_per_mtok: float = 0.0):
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.in_cost = input_cost_per_mtok
        self.out_cost = output_cost_per_mtok

    @abstractmethod
    async def _call(self, system: str, messages: List[Dict[str, Any]], tools: List[ToolSpec],
                    tool_choice: Optional[str], max_tokens: int) -> LLMResponse:
        """One request. Raise ``RetryableError`` for transient failures, ``LLMUnavailable`` for fatal ones."""

    async def acomplete(self, system: str, messages: List[Dict[str, Any]], tools: Optional[List[ToolSpec]] = None,
                        tool_choice: Optional[str] = None, max_tokens: Optional[int] = None,
                        usage: Optional[Usage] = None) -> LLMResponse:
        attempt, delay = 0, 1.0
        while True:
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(self._call(system, messages, tools or [], tool_choice, max_tokens or self.max_tokens),
                                              timeout=self.timeout_s)
                if usage is not None:
                    usage.add(resp, (time.perf_counter() - t0) * 1000, self.in_cost, self.out_cost, attempt)
                return resp
            except (RetryableError, asyncio.TimeoutError) as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise LLMUnavailable(f"{self.name}: out of retries ({type(exc).__name__}: {str(exc)[:200]})") from exc
                wait = getattr(exc, "retry_after", None) or delay * (1 + random.random() * 0.25)
                await asyncio.sleep(min(wait, 30.0))
                delay = min(delay * 2, 16.0)


class RetryableError(RuntimeError):
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


def redact(text: str, secrets: List[str]) -> str:
    for s in secrets:
        if s and len(s) > 4:
            text = text.replace(s, "***")
    return text
