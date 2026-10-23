"""LLM configuration from environment variables (per-agent overrides supported).

Global:     UDRE_LLM_PROVIDER (anthropic | openai | offline), UDRE_LLM_BASE_URL, UDRE_LLM_API_KEY,
            UDRE_LLM_MODEL, UDRE_LLM_TIMEOUT_S, UDRE_LLM_MAX_RETRIES, UDRE_LLM_MAX_TOKENS,
            UDRE_LLM_INPUT_COST_PER_MTOK, UDRE_LLM_OUTPUT_COST_PER_MTOK
Per agent:  UDRE_EXPLORER_LLM_<NAME> / UDRE_HERCULES_LLM_<NAME> override the global value.

With no provider, URL, key or model configured the engine runs in explicit offline
(deterministic) mode.  Model names and credentials are never hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from udre.llm.base import LLMProvider


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("UDRE_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


@dataclass
class LLMSettings:
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float = 60.0
    max_retries: int = 3
    max_tokens: int = 2048
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    def __repr__(self) -> str:  # never print the key
        return f"LLMSettings(provider={self.provider!r}, model={self.model!r}, base_url_host={self.base_url.split('/')[2] if '//' in self.base_url else ''!r})"

    @property
    def configured(self) -> bool:
        return self.provider in ("anthropic", "openai") and bool(self.base_url and self.model and self.api_key)

    @classmethod
    def from_env(cls, agent: Optional[str] = None) -> "LLMSettings":
        _load_dotenv()

        def get(name: str, default: str = "") -> str:
            if agent:
                v = os.environ.get(f"UDRE_{agent.upper()}_LLM_{name}")
                if v:
                    return v
            return os.environ.get(f"UDRE_LLM_{name}", default)

        base_url = get("BASE_URL")
        provider = get("PROVIDER") or ("anthropic" if "/anthropic" in base_url else "openai" if base_url else "offline")
        return cls(provider=provider.lower(), base_url=base_url, model=get("MODEL"), api_key=get("API_KEY"),
                   timeout_s=float(get("TIMEOUT_S", "60")), max_retries=int(get("MAX_RETRIES", "3")),
                   max_tokens=int(get("MAX_TOKENS", "2048")), input_cost_per_mtok=float(get("INPUT_COST_PER_MTOK", "0")),
                   output_cost_per_mtok=float(get("OUTPUT_COST_PER_MTOK", "0")))


def build_provider(settings: LLMSettings) -> Optional[LLMProvider]:
    if not settings.configured:
        return None
    from udre.llm.providers import AnthropicMessagesProvider, OpenAIChatProvider

    kw = dict(timeout_s=settings.timeout_s, max_retries=settings.max_retries, max_tokens=settings.max_tokens,
              input_cost_per_mtok=settings.input_cost_per_mtok, output_cost_per_mtok=settings.output_cost_per_mtok)
    if settings.provider == "anthropic":
        return AnthropicMessagesProvider(settings.base_url, settings.api_key, settings.model, **kw)
    return OpenAIChatProvider(settings.base_url, settings.api_key, settings.model, **kw)
