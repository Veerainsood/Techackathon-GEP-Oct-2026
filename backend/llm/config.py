"""LLM configuration from environment variables (per-agent overrides supported).

Global:     BACKEND_LLM_PROVIDER (anthropic | openai | offline), BACKEND_LLM_BASE_URL, BACKEND_LLM_API_KEY,
            BACKEND_LLM_MODEL, BACKEND_LLM_TIMEOUT_S, BACKEND_LLM_MAX_RETRIES, BACKEND_LLM_MAX_TOKENS,
            BACKEND_LLM_INPUT_COST_PER_MTOK, BACKEND_LLM_OUTPUT_COST_PER_MTOK
Per role:   BACKEND_<ROLE>_LLM_<NAME> overrides the global value; roles are explorer, hercules, adjudicator,
            structured (adjudicator falls back to it) and light.

With no provider, URL, key or model configured the engine runs in explicit offline
(deterministic) mode.  Model names and credentials are never hardcoded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.llm.base import LLMProvider


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if not env.exists() or os.environ.get("BACKEND_IGNORE_DOTENV") == "1":
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith(("BACKEND_", "DAYTONA_")) and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


# Defaults = deployments verified on the configured Foundry resource (scripts/probe_foundry.py, 23 Oct 2026).
# Routing policy: both investigating agents use the strongest model; structured outputs (verdicts, JSON
# extraction) use the OpenAI model; "light" is for cheap understanding/summarisation tasks.
# Override with BACKEND_<ROLE>_LLM_MODEL, e.g. BACKEND_HERCULES_LLM_MODEL.
FOUNDRY_DEFAULT_DEPLOYMENTS = {"explorer": "claude-opus-4-6", "hercules": "claude-opus-4-6", "adjudicator": "gpt-5.6-sol",
                               "intent": "gpt-5.6-sol", "prep": "claude-opus-4-6", "structured": "gpt-5.6-sol",
                               "ledger": "gpt-5.6-sol", "light": "claude-sonnet-5"}
# A role without its own BACKEND_<ROLE>_LLM_* value inherits from this role before the global BACKEND_LLM_* value.
ROLE_FALLBACK = {"adjudicator": "structured", "intent": "structured", "ledger": "structured"}


def foundry_credentials() -> tuple:
    """(endpoint, key) for the Microsoft Foundry resource.

    Prefers BACKEND_FOUNDRY_ENDPOINT / BACKEND_FOUNDRY_API_KEY; falls back to free-form .env lines such as
    ``... urls = https://<resource>.services.ai.azure.com/...`` and ``api key = <key>``.
    """
    _load_dotenv()
    endpoint, key = os.environ.get("BACKEND_FOUNDRY_ENDPOINT", ""), os.environ.get("BACKEND_FOUNDRY_API_KEY", "")
    env = Path(__file__).resolve().parents[2] / ".env"
    if (not endpoint or not key) and env.exists() and os.environ.get("BACKEND_IGNORE_DOTENV") != "1":
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip().lower(), value.strip().strip('"').strip("'")
            if not endpoint and value.startswith("https://") and "services.ai.azure.com" in value:
                endpoint = value
            elif not key and ("api key" in name or name in ("api_key", "apikey", "key")):
                key = value
    return endpoint, key


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

        role = (agent or "").lower()
        chain = [r for r in (role, ROLE_FALLBACK.get(role)) if r]

        def get(name: str, default: str = "") -> str:
            for r in chain:
                v = os.environ.get(f"BACKEND_{r.upper()}_LLM_{name}")
                if v:
                    return v
            return os.environ.get(f"BACKEND_LLM_{name}", default)

        base_url, model, api_key = get("BASE_URL"), get("MODEL"), get("API_KEY")
        if not base_url:  # Microsoft Foundry: one resource, per-role deployments, route chosen by model family
            endpoint, fkey = foundry_credentials()
            if endpoint and fkey:
                host = endpoint.split("/")[2]
                model = model or next((FOUNDRY_DEFAULT_DEPLOYMENTS[r] for r in chain if r in FOUNDRY_DEFAULT_DEPLOYMENTS), "")
                api_key = api_key or fkey
                base_url = (f"https://{host}/anthropic/v1/messages" if model.lower().startswith("claude")
                            else f"https://{host}/openai/v1/chat/completions")
        provider = get("PROVIDER") or ("anthropic" if "/anthropic" in base_url else "openai" if base_url else "offline")
        return cls(provider=provider.lower(), base_url=base_url, model=model, api_key=api_key,
                   timeout_s=float(get("TIMEOUT_S", "60")), max_retries=int(get("MAX_RETRIES", "3")),
                   max_tokens=int(get("MAX_TOKENS", "2048")), input_cost_per_mtok=float(get("INPUT_COST_PER_MTOK", "0")),
                   output_cost_per_mtok=float(get("OUTPUT_COST_PER_MTOK", "0")))


def build_provider(settings: LLMSettings) -> Optional[LLMProvider]:
    if not settings.configured:
        return None
    from backend.llm.providers import AnthropicMessagesProvider, OpenAIChatProvider

    kw = dict(timeout_s=settings.timeout_s, max_retries=settings.max_retries, max_tokens=settings.max_tokens,
              input_cost_per_mtok=settings.input_cost_per_mtok, output_cost_per_mtok=settings.output_cost_per_mtok)
    if settings.provider == "anthropic":
        return AnthropicMessagesProvider(settings.base_url, settings.api_key, settings.model, **kw)
    return OpenAIChatProvider(settings.base_url, settings.api_key, settings.model, **kw)
