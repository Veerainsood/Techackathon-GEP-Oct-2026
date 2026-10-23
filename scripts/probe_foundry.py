"""Probe Microsoft Foundry deployments without printing credentials.

Reads UDRE_FOUNDRY_ENDPOINT / UDRE_FOUNDRY_API_KEY (or the legacy free-form .env lines) and tries candidate
deployment names on the Anthropic Messages route and the OpenAI-compatible route. Prints only status codes
and error codes.  Usage: python scripts/probe_foundry.py [deployment ...]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from udre.llm.config import foundry_credentials  # noqa: E402

CANDIDATES = sys.argv[1:] or [
    "claude-opus-4-6", "claude-opus-4.6", "claude-opus-4-6-1", "claude-sonnet-5", "claude-sonnet-5-0", "claude-sonnet-4-5",
    "gpt-5.6-sol", "gpt-5-6-sol", "gpt-5.6", "gpt-5", "gpt-5-mini",
]


def main() -> None:
    endpoint, key = foundry_credentials()
    if not endpoint or not key:
        print("no Foundry endpoint/key configured")
        return
    host = urlparse(endpoint).hostname
    print("resource host:", host.split(".")[0][:6] + "***." + ".".join(host.split(".")[1:]))
    anth = f"https://{host}/anthropic/v1/messages"
    oai = f"https://{host}/openai/v1/chat/completions"
    with httpx.Client(timeout=30) as c:
        for dep in CANDIDATES:
            r = c.post(anth, headers={"x-api-key": key, "api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": dep, "max_tokens": 5, "messages": [{"role": "user", "content": "Reply OK"}]})
            code = r.json().get("error", {}).get("code") if r.status_code >= 400 and "json" in r.headers.get("content-type", "") else ""
            print(f"anthropic  {dep:<20} HTTP {r.status_code} {code or ''} {r.text[:120] if r.status_code >= 400 else ''}".replace(key, "***"))
            r = c.post(oai, headers={"api-key": key, "authorization": f"Bearer {key}"},
                       json={"model": dep, "max_completion_tokens": 16, "messages": [{"role": "user", "content": "Reply OK"}]})
            print(f"openai     {dep:<20} HTTP {r.status_code} {r.text[:120] if r.status_code >= 400 else ''}".replace(key, "***"))


if __name__ == "__main__":
    main()
